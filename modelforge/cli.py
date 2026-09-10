"""CLI, local JSON/SSE API, and reproducible gateway demo."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sys
from .config import demo_gateway, load_gateway
from .evaluation import evaluate, compare
from .report import render
from .types import Request, GatewayError

ROOT = Path(__file__).resolve().parent


def dump(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def demo(output):
    gateway = demo_gateway(sleep=lambda _: None)
    scenarios = []
    def run(title, description, request):
        events = []
        row = {"title": title, "description": description, "request": asdict(request), "events": events}
        try:
            for event in gateway.stream(request):
                events.append(event.to_dict())
                if event.type == "done":
                    row["result"] = event.data
        except GatewayError as exc:
            row["error"] = exc.code
        scenarios.append(row)
    run("01 / Prefer quality", "Task-specific quality scores select the premium fixture.", Request("What is the capital of France?", policy="quality"))
    run("02 / Serve from cache", "The identical request avoids another provider call and costs zero.", Request("What is the capital of France?", policy="quality"))
    run("03 / Budget-driven fallback", "A zero-dollar budget eliminates paid routes before dispatch.", Request("Explain a circuit breaker.", policy="quality", max_cost_usd=0))
    gateway.providers["scripted-premium"].failures = 2
    run("04 / Recover from an outage", "Two injected 503 responses trigger retry, open the circuit, and fail over.", Request("Explain a circuit breaker in one sentence.", policy="quality", cache=False))
    run("05 / Respect the open circuit", "The unhealthy route is skipped on the next request.", Request("What is the capital of France?", policy="quality", cache=False))
    schema = {"type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string"}}, "required": ["name", "role"], "additionalProperties": False}
    run("06 / Validate typed output", "JSON is buffered and validated before delivery.", Request("Extract name and role. Ada Lovelace is an engineer.", task="extract", schema=schema))
    run("07 / Reject an impossible budget", "No provider is called when the token allowance cannot fit the request.", Request("Explain a circuit breaker.", max_tokens=1))
    evaluation = evaluate(demo_gateway(), ROOT / "evals/dataset.jsonl")
    regressions = compare(evaluation, json.loads((ROOT / "evals/baseline.json").read_text()))
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "scenarios": scenarios, "state": gateway.state(), "evaluation": evaluation, "regressions": regressions}
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    dump(output / "report.json", report)
    dump(output / "evaluation.json", evaluation)
    render(report, output / "report.html")
    print(f"Demo report saved to {output / 'report.html'}")
    print(f"Evaluation {evaluation['metrics']['pass_rate']:.0%} passing; regression gate {'PASS' if not regressions else 'FAIL'}")
    return int(bool(regressions))


def handler_for(gateway):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Prompt bodies and credentials do not enter access logs.

        def send_json(self, status, data):
            body = json.dumps(data, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self.send_json(200, {"status": "ok"})
            else:
                self.send_json(404, {"error": "not_found"})

        def do_POST(self):
            streaming = self.path == "/v1/stream"
            if self.path not in ("/v1/complete", "/v1/stream"):
                self.send_json(404, {"error": "not_found"})
                return
            # Local service intentionally exposes neither CORS nor remote binding.
            if self.headers.get("Origin"):
                self.send_json(403, {"error": "browser_origin_rejected"})
                return
            try:
                if self.headers.get("Transfer-Encoding"):
                    raise ValueError("Unsupported transfer encoding")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise ValueError("Body size out of range")
                self.connection.settimeout(10)
                raw = self.rfile.read(length)
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError("Expected object")
                request = Request(**payload)
            except (ValueError, TypeError, GatewayError, RecursionError, OSError):
                self.send_json(400, {"error": "invalid_request"})
                return
            if not streaming:
                try:
                    self.send_json(200, gateway.complete(request))
                except GatewayError as exc:
                    status = 429 if exc.code == "rate_limited" else 502 if exc.code in ("partial_stream", "all_providers_failed") else 422
                    self.send_json(status, {"error": exc.code})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            stream = gateway.stream(request)
            def write(event, data):
                self.wfile.write(("event: " + event + "\ndata: " + json.dumps(data) + "\n\n").encode())
                self.wfile.flush()
            try:
                for event in stream:
                    write(event.type, event.data)
            except GatewayError as exc:
                write("error", {"code": exc.code})
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass
            finally:
                stream.close()
    return Handler


def main():
    parser = argparse.ArgumentParser(description="ModelForge LLM gateway and evaluation runner")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("demo")
    d.add_argument("--output", default="runs/demo")
    e = sub.add_parser("eval")
    e.add_argument("--dataset", default=str(ROOT / "evals/dataset.jsonl"))
    e.add_argument("--baseline", default=str(ROOT / "evals/baseline.json"))
    e.add_argument("--output", default="runs/evaluation.json")
    e.add_argument("--config")
    e.add_argument("--judge-config")
    e.add_argument("--no-baseline", action="store_true", help="For live exploratory evaluation only")
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument("--config")
    c = sub.add_parser("complete")
    c.add_argument("prompt")
    c.add_argument("--task", default="general")
    c.add_argument("--policy", choices=["balanced", "quality", "cost", "latency"], default="balanced")
    c.add_argument("--config")
    c.add_argument("--stream", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "demo":
            return demo(args.output)
        gateway = load_gateway(args.config) if args.config else demo_gateway()
        if args.command == "eval":
            if args.no_baseline and not args.config:
                parser.error("--no-baseline requires an explicit live --config")
            result = evaluate(gateway, args.dataset, mode="live" if args.config else "scripted", judge=load_gateway(args.judge_config) if args.judge_config else None)
            dump(args.output, result)
            failures = [] if args.no_baseline else compare(result, json.loads(Path(args.baseline).read_text()))
            print(json.dumps({"metrics": result["metrics"], "regressions": failures}, indent=2))
            return int(bool(failures) or result["metrics"]["pass_rate"] < 1)
        if args.command == "serve":
            server = HTTPServer(("127.0.0.1", args.port), handler_for(gateway))
            print(f"ModelForge {'live' if args.config else 'scripted demo'} API at http://127.0.0.1:{args.port}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
        request = Request(args.prompt, task=args.task, policy=args.policy)
        if args.stream:
            for event in gateway.stream(request):
                print(json.dumps(event.to_dict()), flush=True)
        else:
            print(json.dumps(gateway.complete(request), indent=2))
        return 0
    except (GatewayError, ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"error": exc.code if isinstance(exc, GatewayError) else "configuration_error", "message": str(exc) if isinstance(exc, GatewayError) else "Check file paths and configuration fields"}), file=sys.stderr)
        return 1
