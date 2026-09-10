# Implementation status

This reference implementation includes tested orchestration and reproducible offline evidence. Live provider acceptance testing remains a separate milestone.

| Capability | Current implementation | Verification and limit |
| --- | --- | --- |
| Provider-neutral gateway | Shared requests, events, and normalized results | Local API integration tests |
| OpenAI, Anthropic, Gemini, local models | Four native streaming HTTP adapters | Mocked streams pass; live model calls unverified |
| Streaming | Incremental plain text through CLI and SSE API | Protocol tests; no failover after partial delivery |
| Typed responses | Dataclasses and runtime JSON schema subset | Invalid values rejected; no native constrained decoding |
| Retries and fallback | Bounded retry and cross-model fallback | Fault-injection tests |
| Rate limiting | Process-wide sliding window | Clock-controlled expiry test |
| Circuit breakers | Failure threshold, cooldown, recovery probe | Recovery and bypass tests |
| Caching | TTL and bounded LRU response cache | Cache, expiry, policy separation tests |
| Token and cost budgets | Per-request and session reservations | Unknown usage, cancellation, overrun, and fallback tests |
| Policy routing | Task quality, latency, availability, estimated cost | Deterministic selection tests; configured priors |
| Datasets and automated evaluation | Six versioned fixture cases | Saved offline report |
| Task completion | Required-term checks | Heuristic, not independent task adjudication |
| Semantic quality | Lexical overlap proxy only | Embedding-based semantic evaluation not implemented |
| Structured-output compliance | Local schema validation | Required fields, primitive types, arrays, enums |
| LLM-as-judge | Optional live judge with score validation | Mocked judge contract; no live judge run |
| Latency and cost | Wall-clock timing and usage-based estimates | Fixture timing is not representative of cloud models |
| GitHub Actions regression gate | Test matrix and baseline comparison | [CI runs](https://github.com/mohanvamsikrishna-y/modelforge/actions/workflows/ci.yml) cover Python 3.10, 3.12, and 3.13 |
| Production deployment | Not implemented | Local single-user service only |

The schema subset supports object, array, string, integer, number, boolean, null, enum, properties, required, items, and boolean additionalProperties. Unsupported schema keywords are rejected. Full JSON Schema, unions, references, string patterns, and numeric bounds are not supported.

The server implements its own small API. It does not claim wire compatibility with OpenAI Chat Completions or Responses. There is no tool execution, file ingestion, or multimodal interface.
