# Architecture and design decisions

## Objective

Provide one inspectable control layer for routing, provider failures, and evaluation. The first release prioritizes reproducible evidence and correct failure behavior. It can run offline and exposes optional live provider adapters.

## Delivery plan

| Stage | Deliverable | Acceptance evidence |
| --- | --- | --- |
| 1 | Provider-neutral contracts and routing | Policy and budget selection tests |
| 2 | Resilience and accounting | Retry, circuit recovery, cancellation, partial-stream tests |
| 3 | Four native streaming adapters | Mock HTTP contract tests |
| 4 | Evaluation and baseline comparison | Fixture results and negative regression tests |
| 5 | CLI, local API, report | HTTP integration tests and saved demo |
| 6 | Developer documentation | CI workflow, architecture, scope matrix, walkthrough |

These stages are implemented in the first release. Live provider acceptance, representative datasets, semantic scoring, and production infrastructure remain future milestones.

## Provider boundary

Each adapter accepts a model configuration and a normalized request. It yields `delta` events with text and a final `usage` event. The gateway emits route, attempt, retry, failure, skipped, cache-hit, and done events around that stream.

OpenAI uses the Responses API. Anthropic uses Messages. Gemini uses `streamGenerateContent`. Ollama uses streaming `/api/chat`. SSE is parsed by frame boundaries, including multiline data. Ollama uses newline-delimited JSON.

The adapter must observe the provider's terminal completion marker before it yields final usage. Truncated outputs and missing usage fail closed. Upstream error bodies are not copied into traces.

## Routing policy

The router first removes disabled models, unavailable adapters, open circuits, and candidates that cannot fit the request or remaining session allowance. It then computes a weighted score.

```text
score = quality_weight × task_quality
        − cost_weight × estimated_cost / largest_candidate_cost
        − latency_weight × configured_latency / largest_candidate_latency
```

Balanced routing uses weights of 0.50, 0.25, and 0.25. Quality, cost, and latency modes give the selected dimension full weight. Ties use estimated cost and model ID. Unknown tasks use the model's general score.

Quality scores and latency are configured priors. The demo uses invented values, clearly labeled as fixtures. There is no claim that a named real model is better than another. An operator can replace priors with measurements from live evaluations. Automatic calibration is not implemented.

Relative normalization makes the router easy to inspect, but adding a candidate can change balanced rankings. A production version should use stable normalization bounds and task-specific SLOs.

## Retry and circuit state

HTTP 429 and selected 5xx failures are eligible for one retry by default with a short exponential delay. Network failures are ambiguous and are not retried on the same provider. They may fail over if the remaining budget can cover another reservation. The ambiguous attempt stays accounted.

Each model has a failure count and an open-until time. Two failures open the circuit for 30 seconds by default. After cooldown, the next request probes the model. Success resets its failure count. Because the gateway serializes requests, only one probe is active at a time. There is no separate concurrent half-open state machine.

Circuit state is per model, not per provider account. This is useful when one model is unavailable but another works. It does not model shared account-wide throttling.

## Streaming boundary

Plain-text deltas are forwarded as they arrive. If an attempt fails before any nonempty delta is delivered, the router can switch models. After delivery begins, it raises `partial_stream` and does not switch. Consumers must wait for `done` before treating output as complete.

Structured requests are buffered. The gateway parses JSON and validates the supported schema subset before delivering text. An invalid structured response can fail over because no partial JSON has escaped. Its reported usage still counts.

A client that stops reading the Python generator must close it. The HTTP adapter does this on disconnect. Reservations remain accounted when the final usage is unknown.

## Budgets

Input reservations use UTF-8 prompt bytes plus a fixed overhead of 128. The output reservation uses the requested maximum. These are conservative heuristics for text-only inputs, not tokenizer-backed bounds. Schema instructions count toward the prompt estimate.

Before each attempt, the gateway reserves tokens and estimated dollars against both request and session allowances. A completed attempt replaces its reservation with reported usage. A known rejection refunds the reservation. Ambiguous network failures and unfinished streams retain it.

If reported usage exceeds a budget, the gateway records it and fails the request. It cannot undo a provider charge. The next call sees the reduced allowance. Prices use operator-supplied input and output rates and do not reproduce provider billing tiers, discounts, or taxes.

## Cache and rate limits

A bounded LRU cache uses a fingerprint of every request field and the entire model configuration. The default TTL is 120 seconds and capacity is 128 results. Cache hits add zero usage. Failed or invalid results are not cached. Each result carries the original provider usage, while accounted cost and tokens are zero on a hit.

A sliding 60-second window limits request arrivals, including cache hits and rejected routes. State belongs to one process. There is no tenant identity, distributed rate limiter, or durable ledger.

## Evaluation design

The dataset is JSONL with stable case IDs, prompts, references, required terms, optional schemas, and cost and latency ceilings. The runner disables caching so measurements cover provider execution.

The six demo cases are intentionally tiny. Required terms are a task-completion heuristic. Lexical F1 measures token overlap, so synonymous answers can score poorly. Neither substitutes for human review or semantic evaluation.

An optional live LLM judge receives the prompt, reference, and answer as JSON data with an instruction to ignore embedded commands. Its score must fall between zero and one. This reduces instruction confusion but does not eliminate judge prompt injection or bias. Judge cost is separate from generation cost.

The regression gate requires a matching dataset fingerprint and case set. Minimum quality and maximum cost and latency thresholds are committed separately. CI does not silently regenerate its own baseline. A live result cannot pass against the scripted baseline.

## Production path

The next release should add durable reservations with idempotent settlement, provider-aware token counting, concurrent admission control, per-tenant authentication and quotas, encrypted cache storage, OpenTelemetry, calibrated routing profiles, representative datasets, embedding-based semantic metrics, and live provider smoke tests. Provider SDKs would reduce protocol maintenance as the integration surface grows.

The standard library was chosen for this version so a reviewer can run the demo without dependency installation. The tradeoff is more manual protocol and validation code. The built-in HTTP server is appropriate for a local demo, not a production deployment.
