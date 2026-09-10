"""Synchronous gateway with bounded state and conservative attempt accounting."""
from collections import OrderedDict, deque
from dataclasses import asdict
import hashlib
import json
import threading
import time
from .types import Request, Model, Event, GatewayError, ProviderError, validate, finite_nonnegative


class Gateway:
    def __init__(self, models, providers, *, max_session_usd=5.0, max_session_tokens=100000,
                 requests_per_minute=60, retries=1, circuit_threshold=2, cooldown=30,
                 cache_ttl=120, cache_size=128, clock=time.monotonic, sleep=time.sleep):
        self.models = list(models)
        if len({m.id for m in self.models}) != len(self.models):
            raise ValueError("Model ids must be unique")
        if not all(finite_nonnegative(v) for v in (max_session_usd, cooldown, cache_ttl)) or any(type(v) is not int or v < minimum for v, minimum in ((max_session_tokens, 1), (requests_per_minute, 1), (retries, 0), (circuit_threshold, 1), (cache_size, 0))):
            raise ValueError("Invalid gateway limits")
        self.providers = providers
        self.max_session_usd, self.max_session_tokens = max_session_usd, max_session_tokens
        self.rpm, self.retries = requests_per_minute, retries
        self.threshold, self.cooldown = circuit_threshold, cooldown
        self.cache_ttl, self.cache_size = cache_ttl, cache_size
        self.clock, self.sleep = clock, sleep
        self.spent_usd, self.used_tokens = 0.0, 0
        self.cache = OrderedDict()
        self.arrivals = deque()
        self.circuits = {}
        # One in-flight request avoids racing reservations in this local prototype.
        self.lock = threading.Lock()

    def state(self):
        return {"accounted_usd": self.spent_usd, "accounted_tokens": self.used_tokens,
                "max_session_usd": self.max_session_usd, "cache_entries": len(self.cache),
                "circuits": {k: {"failures": v[0], "open": v[1] > self.clock()} for k, v in self.circuits.items()}}

    def complete(self, request: Request):
        events = list(self.stream(request))
        return {**events[-1].data, "events": [e.to_dict() for e in events[:-1]]}

    def stream(self, request: Request):
        # The consumer must exhaust or close this generator to release the lock.
        with self.lock:
            yield from self._stream(request)

    def _stream(self, request):
        started = self.clock()
        while self.arrivals and self.arrivals[0] <= started - 60:
            self.arrivals.popleft()
        if len(self.arrivals) >= self.rpm:
            raise GatewayError("rate_limited", "Local request rate exceeded")
        self.arrivals.append(started)
        key = hashlib.sha256(json.dumps({"request": asdict(request), "models": [asdict(m) for m in self.models]}, sort_keys=True).encode()).hexdigest()
        cached = self.cache.get(key)
        if request.cache and cached and cached[0] > started:
            self.cache.move_to_end(key)
            result = {**cached[1], "cache_hit": True, "cost_usd": 0.0, "accounted_cost_usd": 0.0,
                      "accounted_tokens": 0, "latency_ms": (self.clock() - started) * 1000}
            yield Event("cache_hit", {"model": result["model"]})
            yield Event("delta", {"text": result["text"]})
            yield Event("done", result)
            return
        if cached:
            del self.cache[key]
        estimate_in = request.estimated_input()
        estimate_tokens = estimate_in + request.max_output_tokens
        candidates = []
        for m in self.models:
            reason = None
            if not m.enabled or m.provider not in self.providers:
                reason = "unavailable"
            elif self.circuits.get(m.id, (0, 0))[1] > started:
                reason = "circuit_open"
            elif m.cost(estimate_in, request.max_output_tokens) > min(request.max_cost_usd, self.max_session_usd - self.spent_usd) + 1e-12:
                reason = "cost_budget"
            elif estimate_tokens > min(request.max_tokens, self.max_session_tokens - self.used_tokens):
                reason = "token_budget"
            if reason:
                yield Event("skipped", {"model": m.id, "reason": reason})
            else:
                candidates.append(m)
        if not candidates:
            raise GatewayError("no_route", "No available model fits the request and session budgets")
        costs = {m.id: m.cost(estimate_in, request.max_output_tokens) for m in candidates}
        max_cost = max(costs.values()) or 1
        max_latency = max(m.latency_ms for m in candidates) or 1
        weights = {"balanced": (0.5, 0.25, 0.25), "quality": (1, 0, 0), "cost": (0, 1, 0), "latency": (0, 0, 1)}[request.policy]
        def score(m):
            q = m.quality.get(request.task, m.quality.get("general", 0))
            return weights[0] * q - weights[1] * costs[m.id] / max_cost - weights[2] * m.latency_ms / max_latency
        candidates.sort(key=lambda m: (-score(m), costs[m.id], m.id))
        yield Event("route", {"policy": request.policy, "task": request.task,
                              "candidates": [{"model": m.id, "score": round(score(m), 4), "estimated_cost_usd": costs[m.id]} for m in candidates]})
        request_cost, request_tokens = 0.0, 0
        for m in candidates:
            for attempt in range(self.retries + 1):
                reservation = costs[m.id]
                if reservation > min(request.max_cost_usd - request_cost, self.max_session_usd - self.spent_usd) + 1e-12 or estimate_tokens > min(request.max_tokens - request_tokens, self.max_session_tokens - self.used_tokens):
                    yield Event("skipped", {"model": m.id, "reason": "remaining_budget"})
                    break
                if self.circuits.get(m.id, (0, 0))[1] > self.clock():
                    break
                self.spent_usd += reservation
                self.used_tokens += estimate_tokens
                request_cost += reservation
                request_tokens += estimate_tokens
                yield Event("attempt", {"model": m.id, "attempt": attempt + 1, "reserved_usd": reservation})
                text, usage, emitted = "", None, False
                settled = False
                try:
                    for event in self.providers[m.provider].stream(m, request):
                        if event.type == "delta":
                            text += event.data["text"]
                            if len(text.encode()) > 1000000:
                                raise ProviderError("response_too_large")
                            # Structured output is buffered until validated.
                            if request.schema is None and event.data["text"]:
                                emitted = True
                                yield event
                        elif event.type == "usage":
                            usage = event.data
                    if usage is None or not text:
                        raise ProviderError("incomplete_response")
                    inp, out = usage.get("input_tokens"), usage.get("output_tokens")
                    if any(type(v) is not int or v < 0 for v in (inp, out)):
                        raise ProviderError("invalid_usage")
                    actual_cost, actual_tokens = m.cost(inp, out), inp + out
                    self.spent_usd += actual_cost - reservation
                    self.used_tokens += actual_tokens - estimate_tokens
                    request_cost += actual_cost - reservation
                    request_tokens += actual_tokens - estimate_tokens
                    # Usage is settled even if schema validation fails.
                    settled = True
                    if request_cost > request.max_cost_usd + 1e-12 or request_tokens > request.max_tokens or self.spent_usd > self.max_session_usd + 1e-12 or self.used_tokens > self.max_session_tokens:
                        raise GatewayError("budget_exceeded", "Reported usage exceeded the estimate; session accounting updated")
                    parsed = None
                    if request.schema is not None:
                        try:
                            parsed = json.loads(text)
                            validate(parsed, request.schema)
                        except (ValueError, GatewayError):
                            raise ProviderError("invalid_output", uncertain=True)
                    self.circuits[m.id] = (0, 0)
                    result = {"text": text, "parsed": parsed, "provider": m.provider, "model": m.id,
                              "usage": usage, "cost_usd": actual_cost, "accounted_cost_usd": request_cost,
                              "accounted_tokens": request_tokens, "cache_hit": False,
                              "latency_ms": (self.clock() - started) * 1000}
                    if request.cache and self.cache_size > 0:
                        self.cache[key] = (self.clock() + self.cache_ttl, result)
                        while len(self.cache) > self.cache_size:
                            self.cache.popitem(last=False)
                    if request.schema is not None:
                        yield Event("delta", {"text": text})
                    yield Event("done", result)
                    return
                except ProviderError as exc:
                    if not exc.uncertain and not settled:
                        self.spent_usd -= reservation
                        self.used_tokens -= estimate_tokens
                        request_cost -= reservation
                        request_tokens -= estimate_tokens
                    failures = self.circuits.get(m.id, (0, 0))[0] + 1
                    self.circuits[m.id] = (failures, self.clock() + self.cooldown if failures >= self.threshold else 0)
                    yield Event("failure", {"model": m.id, "code": exc.code, "usage_uncertain": exc.uncertain and not settled})
                    if emitted:
                        raise GatewayError("partial_stream", "Provider failed after output began; stream cannot fail over") from exc
                    if exc.retryable and attempt < self.retries:
                        yield Event("retry", {"model": m.id, "delay_seconds": 0.1 * 2 ** attempt})
                        self.sleep(0.1 * 2 ** attempt)
                        continue
                    break
        raise GatewayError("all_providers_failed", "All eligible attempts failed or exhausted the remaining budget")
