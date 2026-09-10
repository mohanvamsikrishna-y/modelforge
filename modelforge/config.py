import json
from pathlib import Path
from .gateway import Gateway
from .providers import ScriptedProvider, HTTPProvider
from .types import Model


def demo_gateway(**kwargs):
    models = [
        Model("demo-premium", "scripted-premium", "fixture-v1", 3, 12, {"general": .96, "extract": .99}, 750),
        Model("demo-fast", "scripted-fast", "fixture-v1", .3, 1.2, {"general": .86, "extract": .82}, 150),
        Model("demo-local", "scripted-local", "fixture-v1", 0, 0, {"general": .68, "extract": .70}, 450),
    ]
    return Gateway(models, {m.provider: ScriptedProvider() for m in models}, **kwargs)


def load_gateway(path):
    config = json.loads(Path(path).read_text())
    for model in config["models"]:
        if model.get("enabled", True) and model["provider"] != "ollama" and not {"input_rate", "output_rate"} <= model.keys():
            raise ValueError("Live cloud models require explicit input_rate and output_rate")
    models = [Model(**m) for m in config["models"]]
    providers = {m.provider: HTTPProvider(m.provider) for m in models if m.enabled}
    return Gateway(models, providers, **config.get("limits", {}))
