# Contributing

ModelForge keeps the gateway core independent of provider wire formats. Changes should preserve that boundary and include evidence for any changed failure behavior.

## Local checks

Use Python 3.10 or newer from a source checkout.

```sh
python3 -m unittest discover -s tests -v
python3 -m modelforge eval
python3 -m modelforge demo
```

The tests and default demo run without provider credentials. The API tests briefly bind an ephemeral loopback port.

## Changes worth testing

For a new provider, add mocked streaming fixtures that cover completion, missing usage, truncation, and sanitized errors. For gateway changes, exercise the budget and streaming boundaries. A retry must not erase uncertain usage. A partial plain-text answer must never be combined with another provider's answer.

Baseline updates require an explanation of the dataset change and expected metric differences. Do not regenerate a baseline merely to make a failing check pass. CI should keep using the committed baseline.

## Public artifacts

Keep credentials in environment variables. Local provider configuration belongs in files ending with `.local.json`. Do not commit live prompts, customer data, personal notes, or raw provider errors. Saved evidence should come from the scripted demo and contain synthetic data only.

Describe the behavior changed, why it matters, and the relevant validation in pull requests. Live integration claims should identify what was actually exercised without exposing account details.
