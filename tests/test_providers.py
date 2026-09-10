import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from modelforge.providers import HTTPProvider, frames
from modelforge.types import Model, Request, ProviderError


def sse(items):
    return io.BytesIO(''.join('data: ' + json.dumps(x) + '\n\n' for x in items).encode())


class AdapterTests(unittest.TestCase):
    def call(self, kind, items, ndjson=False):
        captured = []
        def opener(req, timeout):
            captured.append(req)
            return io.BytesIO(('\n'.join(json.dumps(i) for i in items) + '\n').encode()) if ndjson else sse(items)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test", "ANTHROPIC_API_KEY": "test", "GEMINI_API_KEY": "test"}):
            events = list(HTTPProvider(kind, opener=opener).stream(Model("x", kind, "configured-model"), Request("hello")))
        return events, captured[0], json.loads(captured[0].data)

    def test_openai_stream_contract(self):
        events, req, body = self.call("openai", [{"type": "response.output_text.delta", "delta": "hello"}, {"type": "response.completed", "response": {"status": "completed", "usage": {"input_tokens": 2, "output_tokens": 1}}}])
        self.assertEqual(events[-1].data, {"input_tokens": 2, "output_tokens": 1})
        self.assertEqual(body["model"], "configured-model")
        self.assertFalse(body["store"])
        self.assertEqual(req.full_url, "https://api.openai.com/v1/responses")

    def test_anthropic_stream_and_cache_usage(self):
        events, req, body = self.call("anthropic", [{"type": "message_start", "message": {"usage": {"input_tokens": 2, "output_tokens": 0, "cache_read_input_tokens": 3}}}, {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hello"}}, {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}}, {"type": "message_stop"}])
        self.assertEqual(events[-1].data["input_tokens"], 5)
        self.assertEqual(body["max_tokens"], 256)

    def test_gemini_stream_includes_thinking_usage(self):
        events, req, body = self.call("gemini", [{"candidates": [{"content": {"parts": [{"text": "hidden", "thought": True}, {"text": "hello"}]}, "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1, "thoughtsTokenCount": 4}}])
        self.assertEqual(events[0].data["text"], "hello")
        self.assertEqual(events[-1].data["output_tokens"], 5)
        self.assertNotIn("key=", req.full_url)

    def test_ollama_ndjson(self):
        events, req, body = self.call("ollama", [{"message": {"content": "hello"}, "done": False}, {"message": {"content": ""}, "done": True, "done_reason": "stop", "prompt_eval_count": 2, "eval_count": 1}], True)
        self.assertEqual(events[-1].data["output_tokens"], 1)
        self.assertTrue(req.full_url.startswith("http://127.0.0.1"))

    def test_missing_terminal_is_error(self):
        with self.assertRaises(ProviderError):
            self.call("openai", [{"type": "response.output_text.delta", "delta": "hello"}])

    def test_truncation_is_error(self):
        with self.assertRaises(ProviderError):
            self.call("ollama", [{"done": True, "done_reason": "length"}], True)

    def test_error_sanitization_and_retry_classification(self):
        for status, retryable, uncertain in [(401, False, False), (429, True, False), (503, True, True)]:
            def opener(*a, **kw):
                raise HTTPError("secret", status, "secret-token", {}, None)
            with patch.dict(os.environ, {"OPENAI_API_KEY": "secret-token"}):
                with self.assertRaises(ProviderError) as ctx:
                    list(HTTPProvider("openai", opener=opener).stream(Model("x", "openai", "x"), Request("x")))
            self.assertEqual(ctx.exception.retryable, retryable)
            self.assertEqual(ctx.exception.uncertain, uncertain)
            self.assertNotIn("secret", str(ctx.exception))

    def test_missing_credentials_never_opens_network(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ProviderError) as ctx:
                HTTPProvider("openai").build_request(Model("x", "openai", "x"), Request("x"))
        self.assertEqual(ctx.exception.code, "missing_credentials")
        self.assertFalse(ctx.exception.uncertain)

    def test_multiline_sse_and_truncated_frame(self):
        self.assertEqual(list(frames(io.BytesIO(b': heartbeat\ndata: {"a":\ndata: 1}\n\n'))), [{"a": 1}])
        with self.assertRaises(ProviderError):
            list(frames(io.BytesIO(b'data: {"a":1}\n')))
