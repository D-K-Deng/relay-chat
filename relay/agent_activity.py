"""Project public, agent-attributed Responses output into a per-turn activity log."""
import json
import os
import shlex
import subprocess
from uuid import uuid4


def tool_target(tool, args):
    if not isinstance(args, dict):
        return ""
    path = args.get("path", "")
    if tool == "run_terminal" and isinstance(args.get("argv"), list):
        argv = args["argv"]
        if all(isinstance(word, str) for word in argv):
            cmd = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
            return "\n".join(filter(None, [args.get("cwd"), cmd]))
    if tool == "read_file" and isinstance(args.get("start"), int):
        return f'{path}:{args["start"]}'
    return " · ".join(value for value in (path, args.get("pattern"), args.get("query")) if isinstance(value, str) and value)


class AgentStream:
    def __init__(self):
        self.items = {}
        self.owners = {}
        self.session = uuid4().hex
        self.finished = set()

    def route(self, event):
        item = event.get("item") or {}
        index = event.get("output_index")
        provided_id = item.get("id") or event.get("item_id")
        if not provided_id and item.get("type") == "function_call":
            provided_id = item.get("call_id")
        item_id = provided_id or self.items.get(index) or f"{self.session}-{index}"
        if index is not None:
            self.items[index] = item_id
        attr = event.get("agent") or item.get("agent") or {}
        name = attr.get("agent_name") or self.owners.get(item_id, "/root")
        if index is not None or provided_id:
            self.owners[item_id] = name
        return name, item_id

    def updates(self, event, name, item_id):
        kind = event["type"]
        if kind in ("response.completed", "response.failed", "response.incomplete"):
            for index, item in enumerate((event.get("response") or {}).get("output") or []):
                snapshot = {"type": "response.output_item.done", "output_index": index, "item": item}
                yield from self.updates(snapshot, *self.route(snapshot))
            return
        if kind in ("response.output_text.delta", "response.refusal.delta") and name != "/root":
            yield {"name": name, "id": item_id, "delta": event["delta"]}
            return
        if name != "/root" and kind.startswith("response.web_search_call."):
            yield {"name": name, "id": item_id, "kind": "tool", "tool": "web_search", "stage": kind.rsplit(".", 1)[1]}
            return
        if kind not in ("response.output_item.added", "response.output_item.done") or item_id in self.finished:
            return
        item = event["item"]
        typ = item["type"]
        done = kind == "response.output_item.done"
        if done:
            self.finished.add(item_id)
        entry = {"name": name, "id": item_id}
        if typ == "multi_agent_call_output" and item.get("action") == "spawn_agent" and done:
            for part in item.get("output", []):
                if part.get("type") == "output_text":
                    try:
                        result = json.loads(part["text"])
                    except ValueError:
                        continue
                    if isinstance(result, dict) and result.get("task_name"):
                        yield {"name": result["task_name"], "id": item_id, "kind": "spawn"}
        elif typ == "agent_message":
            # Inter-agent messages are encrypted. Only show the delivery metadata.
            if item["author"] != "/root":
                yield entry | {"name": item["author"], "kind": "delivery", "target": item["recipient"]}
            if item["recipient"] != "/root":
                yield entry | {"name": item["recipient"], "kind": "received", "target": item["author"]}
        elif name != "/root":
            if typ == "message":
                fields = {"kind": item.get("phase") or "message"}
                if done:
                    content = "".join(p.get("text") or p.get("refusal") or "" for p in item.get("content", [])
                                      if p.get("type") in ("output_text", "refusal"))
                    if content:
                        fields["text"] = content
                    fields["done"] = item.get("status", "completed") == "completed"
                yield entry | fields
            elif typ == "reasoning":
                yield entry | {"kind": "reasoning"}
            elif typ == "web_search_call":
                action = item.get("action") or {}
                fields = {"kind": "tool", "tool": "web_search"}
                if item.get("status"):
                    fields["stage"] = item["status"]
                detail = action.get("query") or "\n".join(action.get("queries") or []) or action.get("url")
                if detail:
                    fields["target"] = detail
                yield entry | fields
            elif typ in ("function_call", "multi_agent_call"):
                tool = item.get("action") if typ == "multi_agent_call" else item.get("name")
                fields = {"kind": "tool", "tool": tool or typ.removesuffix("_call"), "stage": "requested"}
                if typ == "function_call" and item.get("arguments"):
                    try:
                        fields["target"] = tool_target(tool, json.loads(item["arguments"]))
                    except ValueError:
                        pass  # Arguments may still be streaming.
                yield entry | fields


def record(log, update):
    entries = log.setdefault(update["name"], {})
    entry = entries.setdefault(update["id"], {"kind": "message"})
    if "delta" in update:
        entry["text"] = entry.get("text", "") + update["delta"]
    else:
        entry.update({k: v for k, v in update.items() if k not in ("name", "id")})
