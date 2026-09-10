"""Native streaming adapters. Credentials are read only when an adapter is used."""
import json
import os
import socket
from urllib.request import Request as HTTPRequest, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from .types import Event, ProviderError


def frames(response, ndjson=False):
    """Parse SSE data frames, including multiline data, or Ollama NDJSON."""
    data = []
    for raw in response:
        line = raw.decode("utf-8").rstrip("\r\n")
        if len(line) > 1000000:
            raise ProviderError("frame_too_large")
        if ndjson:
            if line.strip():
                yield json.loads(line)
        elif not line:
            if data:
                payload = "\n".join(data)
                data = []
                if payload != "[DONE]":
                    yield json.loads(payload)
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
    if data:
        raise ProviderError("truncated_frame")


class HTTPProvider:
    def __init__(self, kind, *, opener=urlopen, timeout=60):
        if kind not in ("openai", "anthropic", "gemini", "ollama"):
            raise ValueError("Unsupported provider")
        self.kind, self.opener, self.timeout = kind, opener, timeout

    def build_request(self, model, request):
        prompt = request.wire_prompt()
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.kind != "ollama":
            key = os.environ.get({"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}[self.kind])
            if not key:
                raise ProviderError("missing_credentials", uncertain=False)
        if self.kind == "openai":
            url = "https://api.openai.com/v1/responses"
            headers["Authorization"] = "Bearer " + key
            body = {"model": model.model, "input": prompt, "max_output_tokens": request.max_output_tokens, "stream": True, "store": False}
        elif self.kind == "anthropic":
            url = "https://api.anthropic.com/v1/messages"
            headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
            body = {"model": model.model, "messages": [{"role": "user", "content": prompt}], "max_tokens": request.max_output_tokens, "stream": True}
        elif self.kind == "gemini":
            url = "https://generativelanguage.googleapis.com/v1beta/models/" + quote(model.model, safe="") + ":streamGenerateContent?alt=sse"
            headers["x-goog-api-key"] = key
            body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": request.max_output_tokens}}
        else:
            # Fixed loopback endpoint intentionally avoids accepting arbitrary URLs.
            url = "http://127.0.0.1:11434/api/chat"
            headers["Accept"] = "application/x-ndjson"
            body = {"model": model.model, "messages": [{"role": "user", "content": prompt}], "stream": True, "options": {"num_predict": request.max_output_tokens}}
        return HTTPRequest(url, data=json.dumps(body).encode(), headers=headers, method="POST")

    def stream(self, model, request):
        http_request = self.build_request(model, request)
        usage, terminal = {}, False
        try:
            with self.opener(http_request, timeout=self.timeout) as response:
                for item in frames(response, self.kind == "ollama"):
                    if item.get("error") or item.get("type") in ("error", "response.failed", "response.incomplete"):
                        raise ProviderError("upstream_error")
                    delta = ""
                    if self.kind == "openai":
                        if item.get("type") == "response.output_text.delta":
                            delta = item["delta"]
                        if item.get("type") == "response.completed":
                            result = item["response"]
                            if result.get("status") != "completed":
                                raise ProviderError("incomplete_response")
                            usage = result["usage"]
                            terminal = True
                    elif self.kind == "anthropic":
                        if item.get("type") == "message_start":
                            usage = dict(item["message"]["usage"])
                        if item.get("type") == "content_block_delta":
                            delta = item.get("delta", {}).get("text", "")
                        if item.get("type") == "message_delta":
                            if item.get("delta", {}).get("stop_reason") not in (None, "end_turn", "stop_sequence"):
                                raise ProviderError("incomplete_response")
                            usage.update(item.get("usage", {}))
                        if item.get("type") == "message_stop":
                            terminal = True
                    elif self.kind == "gemini":
                        for candidate in item.get("candidates", []):
                            delta += "".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", []) if not p.get("thought"))
                            if "finishReason" in candidate:
                                if candidate["finishReason"] != "STOP":
                                    raise ProviderError("incomplete_response")
                                terminal = True
                        if "usageMetadata" in item:
                            meta = item["usageMetadata"]
                            usage = {"input_tokens": meta["promptTokenCount"], "output_tokens": meta.get("candidatesTokenCount", 0) + meta.get("thoughtsTokenCount", 0)}
                    else:
                        delta = item.get("message", {}).get("content", "")
                        if item.get("done"):
                            if item.get("done_reason", "stop") != "stop":
                                raise ProviderError("incomplete_response")
                            usage = {"input_tokens": item["prompt_eval_count"], "output_tokens": item["eval_count"]}
                            terminal = True
                    if delta:
                        if not isinstance(delta, str):
                            raise ProviderError("invalid_delta")
                        yield Event("delta", {"text": delta})
            if not terminal or not usage:
                raise ProviderError("incomplete_response")
            if self.kind == "anthropic":
                # Cache reads/writes count toward tokens. Uniform rates omit cache price tiers.
                usage["input_tokens"] += usage.get("cache_creation_input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
            yield Event("usage", {"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"]})
        except HTTPError as exc:
            # Never expose response bodies, headers, URLs, or credentials in traces.
            raise ProviderError("http_" + str(exc.code), retryable=exc.code in (429, 500, 502, 503, 504), uncertain=exc.code >= 500) from None
        except (URLError, socket.timeout, TimeoutError, OSError):
            raise ProviderError("network_error", retryable=False) from None
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise ProviderError("malformed_response") from None


class ScriptedProvider:
    """A deterministic fixture, not an LLM or a quality benchmark."""
    def __init__(self, failures=0):
        self.failures = failures
        self.calls = 0

    def stream(self, model, request):
        self.calls += 1
        if self.failures > 0:
            self.failures -= 1
            raise ProviderError("http_503", retryable=True, uncertain=False)
        prompt = request.prompt.lower()
        if request.task == "extract":
            text = json.dumps({"name": "Ada Lovelace", "role": "engineer"})
        elif "capital" in prompt:
            text = "Paris is the capital of France."
        elif request.task == "classify":
            text = "positive" if "love" in prompt else "negative"
        elif request.task == "summarize":
            text = "ModelForge routes requests across providers, applies budgets, and evaluates response quality."
        else:
            text = "A circuit breaker temporarily stops calls to a failing provider and probes recovery after a cooldown."
        if len(text.split()) > request.max_output_tokens:
            raise ProviderError("incomplete_response", uncertain=False)
        words = text.split(" ")
        for index, word in enumerate(words):
            yield Event("delta", {"text": word + (" " if index < len(words) - 1 else "")})
        yield Event("usage", {"input_tokens": len(request.wire_prompt().split()), "output_tokens": len(text.split())})
