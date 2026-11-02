"""Read only the visible conversation in Codex rollout JSONL files."""
import json
import re
from datetime import datetime, timezone

UUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
ROLLOUT = re.compile(r"^rollout-.*-(" + UUID + r")\.jsonl$")


def files(home):
    for folder in ("sessions", "archived_sessions"):
        for p in (home / folder).rglob("rollout-*.jsonl"):
            match = ROLLOUT.match(p.name)
            if match:
                yield match[1].lower(), p


def records(lines):
    for line in lines:
        try:
            obj = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(obj, dict):
            yield obj


def sessions(home):
    names = {}
    index = home / "session_index.jsonl"
    if index.is_file():
        with index.open("rb") as f:
            for obj in records(f):
                names[obj.get("id")] = obj.get("thread_name") or obj.get("title")
    items = []
    for key, p in files(home):
        stamp = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()
        items.append({"id": key, "title": names.get(key) or "Codex · " + p.name[8:24].replace("T", " "),
                      "updated": stamp, "archived": "archived_sessions" in p.relative_to(home).parts})
    return sorted(items, key=lambda item: item["updated"], reverse=True)


def session_path(home, sid):
    return next((p for key, p in files(home) if key == sid), None)


def visible(lines):
    # Rollouts can record the same visible turn in up to three representations.
    result, batch = [], []
    wrappers = ("<environment_context>", "<permissions instructions>", "<external_codex_apps_open_page>",
                "<INSTRUCTIONS>", "# AGENTS.md instructions", "<subagent_notification>", "<user_shell_command>")

    def flush():
        chosen = []
        for role in ("user", "assistant"):
            candidates = [row for row in batch if row[2] == role]
            if candidates:
                best = max(row[1] for row in candidates)
                chosen.extend(row for row in candidates if row[1] == best)
        for _, _, role, text in sorted(chosen):
            result.append({"role": role, "text": text})
        batch.clear()

    def add(rank, role, text):
        if not isinstance(text, str) or not text.strip():
            return
        text = text.strip()
        if role == "user" and text.startswith(wrappers):
            return
        batch.append((len(batch), rank, role, text))

    def parts(obj, role):
        return "\n".join(v["text"] for v in obj.get("content", []) if isinstance(v, dict)
                         and str(v.get("type")).lower() in ("input_text", "output_text", "text")
                         and isinstance(v.get("text"), str)
                         and not (role == "user" and v["text"].lstrip().startswith(wrappers)))

    for obj in records(lines):
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue
        kind, sub = obj.get("type"), payload.get("type")
        if kind == "event_msg" and sub == "task_started":
            flush()
        elif kind == "event_msg" and sub == "item_completed":
            item = payload.get("item") or {}
            role = {"UserMessage": "user", "AgentMessage": "assistant"}.get(item.get("type"))
            if role and item.get("phase") != "commentary":
                add(3, role, parts(item, role))
        elif kind == "event_msg" and sub == "user_message":
            add(2, "user", payload.get("message"))
        elif kind == "event_msg" and sub == "agent_message" and payload.get("phase") != "commentary":
            add(2, "assistant", payload.get("message"))
        elif kind == "response_item" and sub == "message" and payload.get("role") in ("user", "assistant"):
            if payload.get("phase") == "commentary":
                continue
            role = payload["role"]
            add(1, role, parts(payload, role))
        elif kind == "event_msg" and sub == "task_complete":
            if not any(row[2] == "assistant" for row in batch):
                add(1, "assistant", payload.get("last_agent_message"))
            flush()
    flush()
    return result
