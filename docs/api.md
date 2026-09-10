# Local API

Start the offline service with `python3 -m modelforge serve`. Add `--config config.local.json` to use configured live providers. It listens on `127.0.0.1`, port 8787 by default.

## Endpoints

| Method and path | Result |
| --- | --- |
| GET `/health` | A small JSON health response |
| POST `/v1/complete` | One normalized response including its trace |
| POST `/v1/stream` | Server-sent events ending in done or error |

## Request fields

| Field | Default | Meaning |
| --- | --- | --- |
| prompt | Required | Nonempty text, at most 32000 UTF-8 bytes |
| task | general | Task key for quality lookup |
| policy | balanced | balanced, quality, cost, or latency |
| max_output_tokens | 256 | Output reservation and provider generation limit |
| max_cost_usd | 0.05 | Estimated total allowance across all attempts |
| max_tokens | 8192 | Total token allowance across all attempts |
| schema | null | Supported JSON schema subset for validated output |
| cache | true | Whether response cache lookup and insertion are allowed |

```sh
curl http://127.0.0.1:8787/v1/complete \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Extract name and role. Ada Lovelace is an engineer.","task":"extract","schema":{"type":"object","properties":{"name":{"type":"string"},"role":{"type":"string"}},"required":["name","role"],"additionalProperties":false}}'
```

## Streaming events

`route` contains the ranked candidates. `skipped` identifies a filtered model. `attempt` records the reservation. `retry` records the retry delay. `failure` identifies an upstream failure and whether usage is unknown. `delta` carries text. `done` includes the final result. `error` terminates an unsuccessful stream.

The final result includes text, parsed JSON if requested, provider, model, provider usage, final-attempt cost, total accounted request cost and tokens, cache status, and end-to-end latency.

A stream can return HTTP 200 and later emit an error event. Clients must require `done` for success. If text was already delivered, it remains provisional when an error arrives. There is no automatic recovery after a partial plain-text stream.

Invalid JSON or request fields return HTTP 400 on either endpoint. Nonstreaming gateway failures use 429 for local rate limiting, 422 for route or budget rejection, and 502 for exhausted providers or incomplete streaming.

## Local scope

The service is serialized and unauthenticated. It binds only to loopback and rejects browser Origin headers. It has no CORS support, background job store, or public deployment configuration. Its state resets when the process stops.
