import json
import unittest
from modelforge import Gateway, Request, Model, GatewayError
from modelforge.config import demo_gateway
from modelforge.providers import ScriptedProvider
from modelforge.types import Event, ProviderError, validate


class Clock:
    now = 100.0
    def __call__(self):
        return self.now


class Stub:
    def __init__(self, events):
        self.events, self.calls = events, 0
    def stream(self, *_):
        self.calls += 1
        for event in self.events:
            if isinstance(event, Exception):
                raise event
            yield event


def good(text="ok", inp=2, out=1):
    return [Event("delta", {"text": text}), Event("usage", {"input_tokens": inp, "output_tokens": out})]


class GatewayTests(unittest.TestCase):
    def test_routing_policies(self):
        for policy, expected in [("quality", "demo-premium"), ("cost", "demo-local"), ("latency", "demo-fast")]:
            self.assertEqual(demo_gateway().complete(Request("capital", policy=policy))["model"], expected)

    def test_budget_filters_before_dispatch(self):
        gateway = demo_gateway()
        result = gateway.complete(Request("capital", policy="quality", max_cost_usd=0))
        self.assertEqual(result["model"], "demo-local")
        self.assertEqual(gateway.providers["scripted-premium"].calls, 0)

    def test_no_route_never_calls_provider(self):
        gateway = demo_gateway()
        with self.assertRaisesRegex(GatewayError, "No available"):
            gateway.complete(Request("hello", max_tokens=1))
        self.assertTrue(all(p.calls == 0 for p in gateway.providers.values()))

    def test_cache_and_policy_separation(self):
        gateway = demo_gateway()
        first = gateway.complete(Request("capital", policy="quality"))
        second = gateway.complete(Request("capital", policy="quality"))
        third = gateway.complete(Request("capital", policy="cost"))
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(second["accounted_tokens"], 0)
        self.assertFalse(third["cache_hit"])
        self.assertEqual(gateway.providers["scripted-premium"].calls, 1)

    def test_cache_ttl_and_bound(self):
        clock = Clock()
        gateway = demo_gateway(clock=clock, cache_ttl=2, cache_size=1)
        gateway.complete(Request("a"))
        clock.now += 3
        self.assertFalse(gateway.complete(Request("a"))["cache_hit"])
        gateway.complete(Request("b"))
        self.assertEqual(len(gateway.cache), 1)

    def test_retry_circuit_and_recovery(self):
        clock = Clock()
        gateway = demo_gateway(clock=clock, sleep=lambda _: None)
        gateway.providers["scripted-premium"].failures = 2
        result = gateway.complete(Request("capital", policy="quality", cache=False))
        self.assertNotEqual(result["model"], "demo-premium")
        self.assertIn("retry", [e["type"] for e in result["events"]])
        self.assertTrue(gateway.state()["circuits"]["demo-premium"]["open"])
        calls = gateway.providers["scripted-premium"].calls
        gateway.complete(Request("capital", policy="quality", cache=False))
        self.assertEqual(gateway.providers["scripted-premium"].calls, calls)
        clock.now += 31
        recovered = gateway.complete(Request("capital", policy="quality", cache=False))
        self.assertEqual(recovered["model"], "demo-premium")
        self.assertFalse(gateway.state()["circuits"]["demo-premium"]["open"])

    def test_partial_stream_never_falls_back(self):
        first = Stub([Event("delta", {"text": "partial"}), ProviderError()])
        second = Stub(good())
        gateway = Gateway([Model("a", "a", "a", quality={"general": 1}), Model("b", "b", "b")], {"a": first, "b": second})
        with self.assertRaises(GatewayError) as ctx:
            list(gateway.stream(Request("x", policy="quality")))
        self.assertEqual(ctx.exception.code, "partial_stream")
        self.assertEqual(second.calls, 0)
        self.assertGreater(gateway.used_tokens, 0)

    def test_unknown_usage_keeps_reservation(self):
        request = Request("x", max_cost_usd=.01)
        bad = Stub([ProviderError("network_error")])
        gateway = Gateway([Model("a", "a", "a", 1, 2)], {"a": bad})
        with self.assertRaises(GatewayError):
            gateway.complete(request)
        self.assertAlmostEqual(gateway.spent_usd, (request.estimated_input() + 2 * request.max_output_tokens) / 1000000)
        self.assertEqual(gateway.used_tokens, request.estimated_input() + request.max_output_tokens)

    def test_known_rejection_refunds_free_model_tokens(self):
        bad = Stub([ProviderError("http_429", uncertain=False)])
        gateway = Gateway([Model("a", "a", "a")], {"a": bad})
        with self.assertRaises(GatewayError):
            gateway.complete(Request("x"))
        self.assertEqual(gateway.used_tokens, 0)

    def test_total_attempt_budget_blocks_fallback(self):
        bad = Stub([ProviderError("network_error")])
        fallback = Stub(good())
        gateway = Gateway([Model("a", "a", "a", quality={"general": 1}), Model("b", "b", "b")], {"a": bad, "b": fallback})
        request = Request("x", max_tokens=400, policy="quality")
        with self.assertRaises(GatewayError):
            gateway.complete(request)
        self.assertEqual(fallback.calls, 0)

    def test_schema_failure_is_billed_then_falls_back_before_output(self):
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}
        gateway = Gateway([Model("a", "a", "a", 1, 1, {"general": 1}), Model("b", "b", "b", 1, 1)], {"a": Stub(good('{"x":"wrong"}')), "b": Stub(good('{"x":2}'))})
        events = list(gateway.stream(Request("x", policy="quality", schema=schema)))
        self.assertEqual([e.data["text"] for e in events if e.type == "delta"], ['{"x":2}'])
        self.assertEqual(events[-1].data["parsed"], {"x": 2})
        self.assertAlmostEqual(gateway.spent_usd, .000006)
        self.assertEqual(gateway.used_tokens, 6)

    def test_rate_limit_recovers(self):
        clock = Clock()
        gateway = demo_gateway(clock=clock, requests_per_minute=1)
        gateway.complete(Request("x"))
        with self.assertRaises(GatewayError) as ctx:
            gateway.complete(Request("x"))
        self.assertEqual(ctx.exception.code, "rate_limited")
        clock.now += 60
        self.assertTrue(gateway.complete(Request("x"))["cache_hit"])

    def test_session_budget(self):
        stub = Stub(good(inp=100, out=100))
        gateway = Gateway([Model("a", "a", "a")], {"a": stub}, max_session_tokens=500)
        gateway.complete(Request("x", cache=False))
        with self.assertRaises(GatewayError):
            gateway.complete(Request("x", cache=False))
        self.assertEqual(stub.calls, 1)

    def test_cancel_preserves_reservation_and_unlocks(self):
        gateway = demo_gateway()
        stream = gateway.stream(Request("x", cache=False))
        while next(stream).type != "delta":
            pass
        stream.close()
        self.assertGreater(gateway.used_tokens, 0)
        self.assertTrue(gateway.lock.acquire(blocking=False))
        gateway.lock.release()

    def test_usage_overrun_is_accounted(self):
        gateway = Gateway([Model("a", "a", "a")], {"a": Stub(good(inp=9999))})
        with self.assertRaises(GatewayError) as ctx:
            gateway.complete(Request("x"))
        self.assertEqual(ctx.exception.code, "budget_exceeded")
        self.assertEqual(gateway.used_tokens, 10000)
        self.assertEqual(len(gateway.cache), 0)

    def test_invalid_requests_and_schemas(self):
        for kw in [{"prompt": ""}, {"prompt": "x", "max_cost_usd": float("nan")}, {"prompt": "x", "max_output_tokens": True}, {"prompt": "x", "schema": {"type": "string", "pattern": "a"}}]:
            with self.assertRaises(GatewayError):
                Request(**kw)
        with self.assertRaises(GatewayError):
            validate(True, {"type": "integer"})
        with self.assertRaises(GatewayError):
            validate({"extra": 1}, {"type": "object", "additionalProperties": False})
