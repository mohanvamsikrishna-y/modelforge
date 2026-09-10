"""Provider-neutral contracts and a deliberately small output schema language."""
from dataclasses import dataclass, field, asdict
import json
import math
from typing import Any


class GatewayError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ProviderError(GatewayError):
    def __init__(self, code="provider_error", retryable=False, uncertain=True):
        super().__init__(code, code.replace("_", " "))
        self.retryable = retryable
        self.uncertain = uncertain


def finite_nonnegative(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


@dataclass(frozen=True)
class Request:
    prompt: str
    task: str = "general"
    policy: str = "balanced"
    max_output_tokens: int = 256
    max_cost_usd: float = 0.05
    max_tokens: int = 8192
    schema: dict | None = None
    cache: bool = True

    def __post_init__(self):
        if not isinstance(self.prompt, str) or not self.prompt.strip() or len(self.prompt.encode()) > 32000:
            raise GatewayError("invalid_request", "Prompt must contain 1 to 32000 bytes")
        if self.policy not in ("balanced", "quality", "cost", "latency"):
            raise GatewayError("invalid_request", "Unknown routing policy")
        if not isinstance(self.task, str) or not self.task or len(self.task) > 64:
            raise GatewayError("invalid_request", "Invalid task")
        if type(self.max_output_tokens) is not int or not 1 <= self.max_output_tokens <= 16384:
            raise GatewayError("invalid_request", "Invalid output token limit")
        if type(self.max_tokens) is not int or not 1 <= self.max_tokens <= 1000000:
            raise GatewayError("invalid_request", "Invalid token budget")
        if not finite_nonnegative(self.max_cost_usd) or type(self.cache) is not bool:
            raise GatewayError("invalid_request", "Invalid budget or cache setting")
        if self.schema is not None:
            check_schema(self.schema)

    def wire_prompt(self):
        if self.schema is None:
            return self.prompt
        return self.prompt + "\nReturn only JSON matching this schema.\n" + json.dumps(self.schema, sort_keys=True)

    def estimated_input(self):
        # Intentionally conservative byte-based estimate, not a provider tokenizer.
        return len(self.wire_prompt().encode("utf-8")) + 128


@dataclass(frozen=True)
class Model:
    id: str
    provider: str
    model: str
    input_rate: float = 0.0
    output_rate: float = 0.0
    quality: dict = field(default_factory=lambda: {"general": 0.8})
    latency_ms: float = 500
    enabled: bool = True

    def __post_init__(self):
        if not all(isinstance(v, str) and v for v in (self.id, self.provider, self.model)):
            raise ValueError("Model identifiers must be nonempty strings")
        if not all(finite_nonnegative(v) for v in (self.input_rate, self.output_rate, self.latency_ms)):
            raise ValueError("Rates and latency must be finite and nonnegative")
        if not self.quality or not all(finite_nonnegative(v) and v <= 1 for v in self.quality.values()):
            raise ValueError("Quality scores must be between zero and one")
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be boolean")

    def cost(self, input_tokens, output_tokens):
        return (input_tokens * self.input_rate + output_tokens * self.output_rate) / 1000000


@dataclass
class Event:
    type: str
    data: dict

    def to_dict(self):
        return asdict(self)


def check_schema(schema):
    if not isinstance(schema, dict) or schema.get("type") not in ("object", "array", "string", "integer", "number", "boolean", "null"):
        raise GatewayError("invalid_schema", "Schema requires a supported type")
    allowed = {"type", "enum"}
    if schema["type"] == "object":
        allowed |= {"properties", "required", "additionalProperties"}
        props = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(props, dict) or not isinstance(required, list) or any(not isinstance(k, str) or k not in props for k in required):
            raise GatewayError("invalid_schema", "Invalid object properties or required fields")
        if type(schema.get("additionalProperties", True)) is not bool:
            raise GatewayError("invalid_schema", "additionalProperties must be boolean")
        for sub in props.values():
            check_schema(sub)
    if schema["type"] == "array":
        allowed.add("items")
        check_schema(schema.get("items"))
    if set(schema) - allowed:
        raise GatewayError("invalid_schema", "Unsupported schema keyword")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise GatewayError("invalid_schema", "enum must be a nonempty list")


def validate(value: Any, schema: dict):
    kinds = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool, "null": type(None)}
    kind = schema["type"]
    if not isinstance(value, kinds[kind]) or (kind in ("integer", "number") and (isinstance(value, bool) or not math.isfinite(value))):
        raise GatewayError("invalid_output", "Response does not match schema type")
    if "enum" in schema and not any(type(value) is type(v) and value == v for v in schema["enum"]):
        raise GatewayError("invalid_output", "Response is outside allowed values")
    if kind == "object":
        props = schema.get("properties", {})
        if any(k not in value for k in schema.get("required", [])):
            raise GatewayError("invalid_output", "Response is missing required fields")
        if schema.get("additionalProperties") is False and set(value) - set(props):
            raise GatewayError("invalid_output", "Response has unexpected fields")
        for key in set(value) & set(props):
            validate(value[key], props[key])
    if kind == "array":
        for item in value:
            validate(item, schema["items"])
