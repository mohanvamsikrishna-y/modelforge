# Reproducible demo

The demo runs the real gateway against deterministic provider fixtures. No API keys or network calls are required.

```sh
python3 -m modelforge demo
```

Open `runs/demo/report.html` to inspect the full trace. Machine-readable results are saved beside it.

## Scenarios

| Scenario | Behavior to inspect |
| --- | --- |
| 01 / Prefer quality | Task-specific quality scores select the premium fixture. |
| 02 / Serve from cache | The identical request avoids another provider call and costs zero. |
| 03 / Budget-driven fallback | A zero-dollar budget eliminates paid routes before dispatch. |
| 04 / Recover from an outage | Two injected 503 responses trigger retry, open the circuit, and fail over. |
| 05 / Respect the open circuit | The unhealthy route is skipped on the next request. |
| 06 / Validate typed output | JSON is buffered and validated before delivery. |
| 07 / Reject an impossible budget | No provider is called when the token allowance cannot fit the request. |

## An outage, event by event

The following excerpt comes from the saved outage scenario. Text chunks are omitted to make the control flow easier to follow.

```text
attempt   demo-premium  attempt 1
failure   demo-premium  http_503
retry     demo-premium  delay 0.1 seconds
attempt   demo-premium  attempt 2
failure   demo-premium  http_503
attempt   demo-fast  attempt 1
done      demo-fast
```

The premium fixture rejects two calls. The circuit opens, so the next eligible model completes the request. The next scenario shows the premium route being skipped while its circuit is open.

## Reproduce a budget rejection

```sh
python3 -m modelforge serve
```

In another terminal, send a request with an allowance too small to fit its input.

```sh
curl http://127.0.0.1:8787/v1/complete \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Explain a circuit breaker.","max_tokens":1}'
```

The gateway returns HTTP 422 with `no_route`. No provider runs.

## Verify the regression gate

```sh
python3 -m unittest discover -s tests -p test_evaluation.py -v
python3 -m modelforge eval
```

The tests inject lower quality, higher cost, excessive latency, a changed dataset fingerprint, and a missing case. Each condition is rejected by the comparison function.

## Interpret the results

The six fixture cases validate orchestration and evaluation plumbing. Their perfect lexical scores are expected because responses are scripted. Sub-millisecond fixture timings are not estimates of cloud-model latency. Live model quality requires a representative dataset and independent validation.

[Architecture](architecture.md) · [Live provider guide](live-providers.md) · [Saved run](sample-run/report.json)
