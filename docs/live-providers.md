# Live providers and evaluation

## Configure providers

Copy `config.example.json` to a local configuration file. Replace the model names, enter the current input and output prices in USD per million tokens, and enable the providers you intend to use. The zero prices in the example are placeholders. Quality and latency values are illustrative priors that should be replaced with measured evaluation results.

Export credentials in your shell. Only enabled providers require credentials. Ollama uses the fixed local address `127.0.0.1:11434` and requires an already installed model.

```sh
export OPENAI_API_KEY='your-key'
python3 -m modelforge complete "Explain circuit breakers." --config config.local.json --stream
python3 -m modelforge serve --config config.local.json
```

Live adapters are implemented and tested against mocked HTTP streams. No paid live calls were made for the included evidence. Models, account access, pricing, and provider-specific limits must be validated with your own configuration.

Native structured-output APIs are not enabled. Instead, a schema is included in the prompt and the returned JSON is validated locally. Structured responses are buffered until validation passes. Plain text is delivered incrementally.

## Evaluation and CI

The committed dataset covers question answering, classification, extraction, explanation, and summarization. Evaluations record task completion through required terms, lexical F1 against a reference, JSON schema compliance, latency, and accounted cost. Lexical F1 is a transparent overlap proxy and does not measure semantic equivalence.

An optional live judge evaluates correctness, relevance, and coverage using a separate configured gateway. Judge scores are validated and judge costs are reported separately.

```sh
python3 -m modelforge eval \
  --config config.local.json \
  --judge-config judge.local.json \
  --no-baseline \
  --output runs/live-evaluation.json
```

The scripted baseline cannot be used to certify a live run. Baseline comparison fails on a changed dataset fingerprint, missing cases, lower quality, higher cost, or excessive latency. GitHub Actions runs tests and the regression gate on Python 3.10, 3.12, and 3.13. The workflow exits unsuccessfully on a regression. Repository branch protection must separately require the check to prevent merging.


[Provider protocol references](sources.md) · [Implementation status](status.md)
