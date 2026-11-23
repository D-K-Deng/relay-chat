"""Keep the API's encrypted compacted context alongside the visible transcript."""
import json
import re


class Request(dict):
    """The JSON body plus local context budgeting information."""
    limit = 0
    restored = False
    new_checkpoint = False


def supports_compaction(model):
    version = re.match(r"gpt-(\d+)(?:\.(\d+))?", model)
    return bool(version and tuple(int(n or 0) for n in version.groups()) >= (5, 2))


def estimate(item):
    if isinstance(item.get("content"), list):
        total = 0
        for part in item["content"]:
            if part.get("type") in {"input_image", "input_file"}:
                total += 2048
            else:
                total += len(part.get("text", "").encode()) // 4 + 1
        return total + 8
    return len(json.dumps(item, ensure_ascii=False).encode()) // 4 + 8


def latest_compaction(items):
    for index in range(len(items) - 1, -1, -1):
        item = items[index]
        if item.get("type") == "compaction" and (item.get("agent") or {}).get("agent_name", "/root") == "/root":
            return items[index:]
    return []


def replay(history, model):
    for index in range(len(history) - 1, -1, -1):
        checkpoint = history[index].get("compaction") or {}
        if checkpoint.get("model") == model and checkpoint.get("items"):
            return checkpoint["items"], history[index + 1:]
    return [], history


def prepare_existing(body, compact, emit):
    """Bring a long pre-upgrade transcript into range, keeping its newest turn intact."""
    ceiling = body.limit
    items = body["input"]
    sizes = [estimate(item) for item in items]
    while sum(sizes) > ceiling:
        # Keep instructions at the start and the user's current request at the end.
        boundary, used = 1, sizes[0]
        while boundary < len(items) - 1 and used + sizes[boundary] <= ceiling * .85:
            used += sizes[boundary]
            boundary += 1
        if boundary == 1 or (boundary == 2 and items[1].get("type") == "compaction"):
            raise ValueError("A message exceeds the model's context budget. Shorten it or select a larger model.")
        emit("compacting")
        request = {"model": body["model"], "input": [*items[:boundary], {"type": "compaction_trigger"}],
                   "store": False, "stream": True, "service_tier": body["service_tier"]}
        result = compact(request)
        replacement = latest_compaction(result.get("output_items", []))
        if not replacement:
            raise ValueError("OpenAI did not return a context checkpoint. Retry or choose a shorter conversation context.")
        items[1:boundary] = replacement
        sizes[1:boundary] = [estimate(item) for item in replacement]
        body.restored = True
        body.new_checkpoint = True
