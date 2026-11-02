"""Standard USD token prices used by the optional local credit estimate.

Rates checked 2026-11-02 against the model pages linked in the balance panel.
Values are dollars per million uncached input, cached input, and output tokens.
"""
import re

STANDARD = {
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-5.4-nano": (0.20, 0.02, 1.25),
    "gpt-5.4": (2.50, 0.25, 15.00),
}


def base_model(model):
    return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model)


def estimate_usd(model, usage, tier="default", override=None):
    if not usage or tier not in (None, "default", "standard"):
        return None
    rates = override or STANDARD.get(base_model(model))
    if not rates or "input_tokens" not in usage or "output_tokens" not in usage:
        return None
    inputs, outputs = usage["input_tokens"], usage["output_tokens"]
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
    input_rate, cached_rate, output_rate = rates
    if override is None and base_model(model) == "gpt-5.4" and inputs > 272_000:
        input_rate *= 2
        cached_rate *= 2
        output_rate *= 1.5
    return ((inputs - cached) * input_rate + cached * cached_rate + outputs * output_rate) / 1_000_000
