"""Chat model capabilities for the IDs returned by OpenAI's Models API."""
import json
import re

from .pricing import base_model

# The Models API lists IDs and creation dates. The model pages document the
# Responses capabilities, and the pricing page documents supported tiers.
# https://developers.openai.com/api/docs/models
# https://developers.openai.com/api/docs/pricing
CORE = ("low", "medium", "high")
EXTENDED = (*CORE, "xhigh")
MAXIMUM = (*EXTENDED, "max")
EFFORTS = {
    "gpt-5": ("minimal", *CORE),
    "gpt-5-mini": ("minimal", *CORE),
    "gpt-5-nano": ("minimal", *CORE),
    "gpt-5-pro": ("high",),
    "gpt-5.1": ("none", *CORE),
    "gpt-5.2": ("none", *EXTENDED),
    "gpt-5.2-pro": ("medium", "high", "xhigh"),
    "gpt-5.3-codex": EXTENDED,
    "gpt-5.4": ("none", *EXTENDED),
    "gpt-5.4-mini": ("none", *EXTENDED),
    "gpt-5.4-nano": ("none", *EXTENDED),
    "gpt-5.4-pro": ("medium", "high", "xhigh"),
    "gpt-5.5": ("none", *EXTENDED),
    "gpt-5.6": ("none", *MAXIMUM),
    "gpt-5.6-sol": ("none", *MAXIMUM),
    "gpt-5.6-terra": ("none", *MAXIMUM),
    "gpt-5.6-luna": ("none", *MAXIMUM),
    "gpt-6-astra": MAXIMUM,
    "gpt-6-sol": ("none", *MAXIMUM),
    "gpt-6-luna": ("none", *MAXIMUM),
    "gpt-6.1-sol": MAXIMUM,
    "gpt-6.1-terra": ("none", *MAXIMUM),
    "gpt-6.2-astra": MAXIMUM,
    "gpt-6.2-sol": MAXIMUM,
    "o1": CORE,
    "o3": CORE,
    "o3-mini": CORE,
    "o4-mini": CORE,
}
FAST = {
    "gpt-6.2-astra", "gpt-6.2-sol", "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-astra",
    "gpt-6-sol", "gpt-6-luna", "gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna",
    "gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.2", "gpt-5.1", "gpt-5", "gpt-5-mini",
    "gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini", "o3",
}
ULTRAFAST = {"gpt-6.2-astra", "gpt-6.2-sol", "gpt-6.1-sol", "gpt-6-astra"}
# Intersection of Codex Ultra presets and the hosted Responses Multi-agent beta.
# https://developers.openai.com/api/docs/guides/responses-multi-agent
ULTRA_EFFORT = {
    "gpt-6.2-astra": "xhigh", "gpt-6.2-sol": "xhigh", "gpt-6.1-sol": "xhigh",
    "gpt-5.6": "max", "gpt-5.6-sol": "max", "gpt-5.6-terra": "max",
}
FLEX = {
    "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna",
    "gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna",
    "gpt-5.5", "gpt-5.4", "gpt-5.4-pro", "gpt-5.4-mini", "gpt-5.4-nano",
    "gpt-5.2", "gpt-5.1", "gpt-5", "gpt-5-mini", "gpt-5-nano", "o3",
}
# Compatible chat families, excluding specialized IDs such as search and audio.
CHAT = re.compile(r"gpt-(?:4o|4\.1|[5-9](?:\.\d+)?)(?:-(?:mini|nano|astra|sol|terra|luna))?")
WEB_SEARCH = {
    "chat-latest", "gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini",
    "gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5.1", "gpt-5.2", "gpt-5.3-codex", "gpt-5.4", "gpt-5.4-mini",
    "gpt-5.4-nano", "gpt-5.4-pro",
    "gpt-5.5", "gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-6-astra",
    "gpt-6-sol", "gpt-6-luna", "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6.2-astra", "gpt-6.2-sol", "o3",
}
# Published Codex defaults and its 95% usable-input allowance. Prefer the
# local Codex model catalog when it is available.
CODEX_CONTEXT = {"gpt-6.2-astra": 258_400, "gpt-6.2-sol": 475_000, "gpt-6.1-sol": 475_000}


def codex_model_defaults(directory):
    limits, ultra = CODEX_CONTEXT.copy(), ULTRA_EFFORT.copy()
    try:
        catalog = json.loads((directory / "models_cache.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return limits, ultra
    for model in catalog.get("models", []):
        slug = model["slug"]
        if window := model.get("context_window"):
            limits[slug] = window * (model.get("effective_context_window_percent") or 95) // 100
        if slug in ULTRA_EFFORT:
            supported = [level["effort"] for level in model.get("supported_reasoning_levels", [])
                         if level["effort"] in EFFORTS[slug]]
            preferred = model.get("multi_agent_reasoning_effort")
            if supported:
                fallback = "max" if "max" in supported else supported[-1]
                ultra[slug] = preferred if preferred in supported else fallback
    ultra["gpt-5.6"] = ultra["gpt-5.6-sol"]
    return limits, ultra


def capabilities(model):
    family = base_model(model)
    tiers = ["default"]
    for name, supported in (("fast", FAST), ("ultrafast", ULTRAFAST), ("flex", FLEX)):
        if family in supported:
            tiers.append(name)
    efforts = ["auto", *EFFORTS.get(family, ())]
    if family in ULTRA_EFFORT:
        efforts.append("ultra")
    known_chat = family in EFFORTS or family in {
        "chat-latest", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano", "gpt-4o", "gpt-4o-mini",
    }
    return {"id": model, "efforts": efforts, "tiers": tiers, "web_search": family in WEB_SEARCH,
            "reasoning_summary": family in EFFORTS and family not in {"o1", "o3-mini"},
            "pdf": known_chat and family != "o3-mini", "workspace_tools": known_chat}


def output_limit(model):
    family = base_model(model)
    if family in {"gpt-4o", "gpt-4o-mini"}:
        return 16_384
    if family in {"gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano"}:
        return 32_768
    if family in {"o1", "o3", "o3-mini", "o4-mini"}:
        return 100_000
    if family == "gpt-5-pro":
        return 272_000
    return 128_000


def chat_catalog(entries):
    chat = []
    for entry in entries:
        model = entry["id"]
        family = base_model(model)
        if CHAT.fullmatch(family) or family in EFFORTS or family == "chat-latest":
            chat.append((model != family, -entry["created"], model))
    return [capabilities(model) for _, _, model in sorted(chat)]


def selections(model, effort, tier):
    options = capabilities(model)
    return {
        "effort": effort if effort in options["efforts"] else "auto",
        "service_tier": tier if tier in options["tiers"] else "default",
    }
