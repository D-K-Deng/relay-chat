"""Published OpenAI rates for the local API credit estimate."""
import re
from datetime import datetime, timezone

PRICE_DATE = "2026-11-22"
SEARCH_CALL_USD = 0.01
ULTRAFAST_PROMOTION_END = datetime(2027, 3, 1, 8, tzinfo=timezone.utc)  # Midnight Pacific time.
SOL_PROMOTION_END = datetime(2026, 12, 1, 8, tzinfo=timezone.utc)

# USD per million input, cached input, and output tokens. None means that no
# cached rate is published for that model. Sources: the official pricing tables
# and the model catalog for legacy models and chat-latest.
# https://developers.openai.com/api/docs/pricing
# https://developers.openai.com/api/docs/models
STANDARD = {
    "gpt-6.2-astra": (8, 0.40, 40),
    "gpt-6.2-sol": (2, 0.10, 10),
    "gpt-6.1-sol": (2, 0.10, 10),
    "gpt-6.1-terra": (1, 0.05, 5),
    "gpt-6-astra": (10, 1, 50),
    "gpt-6-sol": (2, 0.20, 10),
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-5.6-sol": (4, 0.40, 20),  # Promotional rate published through November 30, 2026.
    "gpt-5.6-terra": (2, 0.20, 12),
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
    "gpt-5.5": (5, 0.50, 30),
    "gpt-5.4": (2.50, 0.25, 15),
    "gpt-5.4-pro": (30, None, 180),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-5.4-nano": (0.20, 0.02, 1.25),
    "gpt-5.3-codex": (1.75, 0.175, 14),
    "gpt-5.2": (1.75, 0.175, 14),
    "gpt-5.2-pro": (21, None, 168),
    "gpt-5.1": (1.25, 0.125, 10),
    "gpt-5": (1.25, 0.125, 10),
    "gpt-5-mini": (0.25, 0.025, 2),
    "gpt-5-nano": (0.05, 0.005, 0.40),
    "gpt-5-pro": (15, None, 120),
    "gpt-4.1": (2, 0.50, 8),
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-4.1-nano": (0.10, 0.025, 0.40),
    "gpt-4o": (2.50, 1.25, 10),
    "gpt-4o-mini": (0.15, 0.075, 0.60),
    "chat-latest": (5, 0.50, 30),
    "o1": (15, 7.50, 60),
    "o3": (2, 0.50, 8),
    "o3-mini": (1.10, 0.55, 4.40),
    "o4-mini": (1.10, 0.275, 4.40),
}
MODEL_ALIASES = {"gpt-5.6": "gpt-5.6-sol"}
CACHE_WRITES = {
    "gpt-6.2-astra", "gpt-6.2-sol", "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-astra",
    "gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna",
}
LONG_CONTEXT = CACHE_WRITES | {"gpt-5.5", "gpt-5.4", "gpt-5.4-pro"}
FAST_DOUBLE = CACHE_WRITES | {"gpt-5.4", "gpt-5.4-mini", "gpt-5.2", "gpt-5.1", "gpt-5"}
FAST = {
    "gpt-5.5": (12.50, 1.25, 75),
    "gpt-5-mini": (0.45, 0.045, 3.60),
    "gpt-4.1": (3.50, 0.875, 14),
    "gpt-4.1-mini": (0.70, 0.175, 2.80),
    "gpt-4o": (4.25, 2.125, 17),
    "gpt-4o-mini": (0.25, 0.125, 1),
    "o3": (3.50, 0.875, 14),
}
FLEX_HALF = (CACHE_WRITES - {"gpt-6.2-astra", "gpt-6.2-sol"}) | {
    "gpt-5.5", "gpt-5.4-pro", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.2", "gpt-5.1",
    "gpt-5", "gpt-5-mini", "gpt-5-nano", "o3",
}
ULTRAFAST = {"gpt-6-astra", "gpt-6.1-sol", "gpt-6.2-astra", "gpt-6.2-sol"}
STANDARD_TIERS = (None, "standard", "default")


def base_model(model):
    return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model)


def price_model(model):
    family = base_model(model)
    return MODEL_ALIASES.get(family, family)


def scale(rates, multiplier):
    return tuple(rate * multiplier if rate is not None else None for rate in rates)


def model_rates(model, tier="default", *, input_tokens=0, override=None, at=None):
    """Return the billed tier's rates, or None when no applicable rate is published."""
    if tier in STANDARD_TIERS and override is not None:
        return override
    model = price_model(model)
    at = at or datetime.now(timezone.utc)
    if model == "gpt-5.6-sol" and at >= SOL_PROMOTION_END:
        return None
    standard = STANDARD.get(model)
    if standard is None:
        return None

    long_context = model in LONG_CONTEXT and input_tokens > 272_000
    if tier in STANDARD_TIERS:
        rates = standard
    elif tier in ("fast", "priority"):
        if long_context and model in {"gpt-5.5", "gpt-5.4"}:
            return None  # The Fast pricing table only lists short-context rates for them.
        rates = scale(standard, 2) if model in FAST_DOUBLE else FAST.get(model)
    elif tier == "flex":
        rates = scale(standard, 0.5) if model in FLEX_HALF else None
        if model == "gpt-5.4":
            return (2.50, 0.25, 11.25) if long_context else (1.25, 0.13, 7.50)
    elif tier == "ultrafast" and model in ULTRAFAST:
        promotional = model in {"gpt-6.2-astra", "gpt-6.2-sol"} and at < ULTRAFAST_PROMOTION_END
        rates = scale(standard, 3 if promotional else 6)
    else:
        return None

    if rates is not None and long_context:
        input_rate, cached_rate, output_rate = rates
        return (input_rate * 2, cached_rate * 2 if cached_rate is not None else None, output_rate * 1.5)
    return rates


def estimate_usd(model, usage, tier="default", override=None, *, search_calls=0):
    if model == "gpt-transcribe":
        # File transcription is billed by audio duration in minutes.
        seconds = (usage or {}).get("seconds")
        return seconds * 0.0045 / 60 if seconds is not None else None
    if not usage or "input_tokens" not in usage or "output_tokens" not in usage:
        return None
    inputs, outputs = usage["input_tokens"], usage["output_tokens"]
    rates = model_rates(model, tier, input_tokens=inputs, override=override)
    if rates is None:
        return None
    input_rate, cached_rate, output_rate = rates
    details = usage.get("input_tokens_details") or {}
    cached = details.get("cached_tokens", 0)
    if cached and cached_rate is None:
        return None

    amount = (inputs - cached) * input_rate + cached * (cached_rate or 0) + outputs * output_rate
    # The cached-token metric already excludes cache writes. When retention
    # details are absent, Relay uses the API's default 30-minute retention.
    if price_model(model) in CACHE_WRITES and not (tier in STANDARD_TIERS and override is not None):
        written = details.get("cache_write_tokens", 0)
        written_12h = (details.get("cache_write_tokens_details") or {}).get("ttl_12h_tokens", 0)
        amount += ((written - written_12h) * 0.25 + written_12h * 1.5) * input_rate
    return amount / 1_000_000 + search_calls * SEARCH_CALL_USD
