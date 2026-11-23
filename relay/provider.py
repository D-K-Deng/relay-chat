"""Translate local chat messages to a stateless OpenAI Responses request."""
import json
import re
import time
import urllib.error
import urllib.request
from email.utils import mktime_tz, parsedate_tz

from . import agent_activity, context, models
from .prompts import CHAT_INSTRUCTIONS

ENDPOINT = "https://api.openai.com/v1/responses"
MODELS_ENDPOINT = "https://api.openai.com/v1/models"
PDF_PREFIX = "data:application/pdf;base64,"


def available_models(key):
    request = urllib.request.Request(MODELS_ENDPOINT, headers={"Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return models.chat_catalog(json.load(response)["data"])
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ValueError("OpenAI could not list models. Check the API key and its Models read permission.") from None
        raise ValueError("OpenAI could not list models. Try reloading the list.") from None
    except (OSError, ValueError):
        raise ValueError("Could not reach the OpenAI model list. Check the connection and try again.") from None


def user_message(data):
    text = data.get("text", "")
    attachments = data.get("files") or []
    if not isinstance(text, str) or not isinstance(attachments, list) or len(attachments) > 4:
        raise ValueError("Enter a text message and attach no more than four files.")
    files = []
    for item in attachments:
        kind, value = item.get("kind"), item.get("data", "")
        if not isinstance(value, str):
            raise ValueError("Could not read this attachment.")
        if kind == "image":
            if len(value) > 4_200_000 or not re.match(r"^data:image/(png|jpeg|webp|gif);base64,", value):
                raise ValueError("Choose a PNG, JPEG, WebP, or GIF image up to 3 MB.")
        elif kind == "pdf":
            if not value.startswith(PDF_PREFIX) or len(value) > len(PDF_PREFIX) + 4 * 1024 * 1024:
                raise ValueError("Choose a PDF up to 3 MB.")
        elif kind == "spreadsheet":
            raise ValueError("Attach a CSV as text instead of a workbook.")
        elif kind != "text" or len(value) > 100_000:
            raise ValueError("Text attachments can contain up to 100,000 characters each.")
        files.append({"name": str(item.get("name", "Attachment"))[:200], "kind": kind, "data": value})
    if not text.strip() and not files:
        raise ValueError("Enter a message or add an attachment.")
    return {"role": "user", "text": text.strip(), "files": files}


def message_tokens(message):
    text_bytes = len(message["text"].encode())
    images = 0
    for file in message.get("files", []):
        if file["kind"] in {"image", "pdf", "spreadsheet"}:
            # The API accounts for decoded visual inputs, not their base64 text.
            images += 1
        else:
            text_bytes += len(file["name"].encode()) + len(file["data"].encode()) + 20
    return (text_bytes + 3) // 4 + images * 2048 + 8


def request_body(history, settings, web_search=False, input_limit=None, ultra_effort=None):
    model = settings["model"]
    usable = [m for m in history if m["role"] in ("user", "assistant") and
              (m["text"] or m.get("files") or m.get("compaction") or m.get("steers"))]
    take = settings["context"]
    selected = usable[-take:] if take else usable
    instructions = CHAT_INSTRUCTIONS
    if custom := settings["instructions"].strip():
        instructions += "\n\nUser's standing preferences\n" + custom
    if web_search:
        input_limit = min(input_limit or 128_000, 128_000)
    budget = input_limit or 128_000
    automatic = not take and budget >= 2048 and context.supports_compaction(model)
    checkpoint = []
    if automatic:
        checkpoint, selected = context.replay(selected, model)
    if input_limit and not automatic:
        remaining = input_limit - message_tokens({"text": instructions})
        keep = 0
        for message in reversed(selected):
            remaining -= message_tokens(message)
            if remaining < 0:
                break
            keep += 1
        if not keep and selected:
            raise ValueError("The latest message exceeds the model's input budget. Shorten it or remove an attachment.")
        if keep:
            selected = selected[-keep:]
    options = models.capabilities(model)
    selection = models.selections(model, settings.get("effort", "auto"), settings.get("service_tier", "default"))
    effort = selection["effort"]
    if effort == "ultra":
        instructions += "\n\nDelegate independent parts of the task to subagents when parallel work would improve the result."
    data = [{"role": "developer", "content": instructions}, *checkpoint]
    for m in selected:
        if m["role"] == "assistant":
            for steer in m.get("steers", []):
                if steer.get("status") == "applied":
                    data.append({"role": "user", "content": steer["text"]})
            if m["text"]:
                data.append({"role": "assistant", "content": m["text"]})
            continue
        parts = [{"type": "input_text", "text": m["text"]}] if m["text"] else []
        for f in m.get("files", []):
            if f["kind"] == "image":
                parts.append({"type": "input_image", "image_url": f["data"], "detail": "auto"})
            elif f["kind"] == "pdf":
                if not options["pdf"]:
                    raise ValueError("This conversation includes a PDF. Choose a model that supports PDF inputs.")
                parts.append({"type": "input_file", "filename": f["name"], "file_data": f["data"]})
            elif f["kind"] == "spreadsheet":
                # Older saved messages can contain a workbook from the retired tool.
                parts.append({"type": "input_text", "text": f"Previously attached spreadsheet: {f['name']}"})
            else:
                parts.append({"type": "input_text", "text": f"Attachment: {f['name']}\n\n{f['data']}"})
        data.append({"role": "user", "content": parts})
    body = context.Request(model=model, input=data, stream=True, store=False, truncation="auto")
    body.limit = budget
    body.restored = bool(checkpoint)
    if automatic:
        body["context_management"] = [{"type": "compaction", "compact_threshold": budget * 3 // 4}]
    if settings["max_output"]:
        body["max_output_tokens"] = min(settings["max_output"], models.output_limit(model))
    if web_search:
        if not options["web_search"]:
            raise ValueError("Choose a model that supports web search.")
        if effort == "minimal":
            raise ValueError("Web search needs Low reasoning effort or higher with this model.")
        body.update(tools=[{"type": "web_search"}], tool_choice="required")
    if effort == "ultra":
        body["multi_agent"] = {"enabled": True, "max_concurrent_subagents": 3}
        body["reasoning"] = {"effort": ultra_effort or models.ULTRA_EFFORT[models.base_model(model)]}
    elif effort != "auto":
        body["reasoning"] = {"effort": effort}
    if settings.get("reasoning_summary") and options["reasoning_summary"] and effort not in {"none", "ultra"}:
        body.setdefault("reasoning", {})["summary"] = "auto"
    body["service_tier"] = selection["service_tier"]
    return body


def response_details(response, multi_agent=False):
    text_parts, sources, summaries, subagents = [], [], [], []
    offset = 0
    search_calls = 0
    for item in response.get("output") or []:
        if item["type"] == "web_search_call":
            action = item.get("action") or {}
            search_calls += item.get("status") == "completed" and action.get("type") == "search"
        agent = (item.get("agent") or {}).get("agent_name", "/root")
        if agent != "/root":
            if agent not in subagents:
                subagents.append(agent)
            continue
        if item["type"] == "reasoning":
            summaries.extend(part["text"] for part in item.get("summary") or [] if part["type"] == "summary_text")
        if multi_agent and item.get("phase") == "commentary":
            continue
        if item["type"] != "message":
            continue
        for part in item["content"]:
            if part["type"] == "output_text":
                text = part["text"]
                for citation in part.get("annotations") or []:
                    if citation["type"] != "url_citation" or not citation["url"].startswith(("https://", "http://")):
                        continue
                    sources.append({"url": citation["url"], "title": citation["title"],
                                    "start_index": offset + citation["start_index"],
                                    "end_index": offset + citation["end_index"]})
            elif part["type"] == "refusal":
                text = part["refusal"]
            else:
                continue
            text_parts.append(text)
            offset += len(text)
    tool_usage = (response.get("tool_usage") or {}).get("web_search") or {}
    text = "".join(text_parts)
    details = {"sources": sources, "search_calls": tool_usage.get("num_requests", search_calls),
               "usage": response.get("usage"), "billed_model": response.get("model"),
               "service_tier": response.get("service_tier")}
    if text:
        details["text"] = text
    if summaries:
        details["reasoning_summary"] = "\n\n".join(summaries)
    if subagents:
        details["subagents"] = subagents
    return details


def error_code(err):
    error_type = err.get("type") or ""
    return err.get("code") or (error_type if error_type != "error" and not error_type.startswith("response.") else "")


SPEND_LIMIT_CODES = {"project_spend_limit_exceeded", "organization_spend_limit_exceeded", "organization_usage_limit_exceeded"}
RATE_CODES = {"rate_limit_exceeded", "rate_limit_error", "slow_down"}
DURATION_PART = r"(\d+(?:\.\d+)?)\s*(milliseconds?|minutes?|seconds?|hours?|ms|s|m|h)"


def duration_seconds(value):
    """Read OpenAI's reset durations, e.g. 169ms, 2.5s or 6m0s."""
    if not re.fullmatch(rf"(?:{DURATION_PART}\s*)+", value, re.I):
        return None
    seconds = 0.0
    for amount, unit in re.findall(DURATION_PART, value.lower()):
        multiplier = .001 if unit == "ms" or unit.startswith("milli") else {"h": 3600, "m": 60, "s": 1}[unit[0]]
        seconds += float(amount) * multiplier
    return seconds


def temporary_rate_limit(err, status, headers):
    code = error_code(err)
    message = err.get("message") or ""
    if message.lower().startswith("request too large"):
        return False  # Waiting cannot make this single request fit the bucket.
    if code:
        return code in RATE_CODES
    return status == 429 and (bool(re.search(r"rate limit|too many requests", message, re.I))
                              or any(name.lower() in {"retry-after", "retry-after-ms"} for name in headers))


def rate_limit_info(err, status, headers):
    if not temporary_rate_limit(err, status, headers):
        return None
    message = err.get("message") or ""
    headers = {name.lower(): str(value).strip() for name, value in headers.items()}
    metric = re.search(r"\b(TPM|RPM|TPD|RPD)\b", message, re.I)
    result = {"retry_after": None}
    if metric:
        result["metric"] = metric[0].upper()
    retry_after = headers.get("retry-after", "")
    if re.fullmatch(r"\d+(?:\.\d+)?", retry_after):
        result["retry_after"] = float(retry_after)
    elif retry_after and (date := parsedate_tz(retry_after)):
        result["retry_after"] = max(0.0, mktime_tz(date) - time.time())
    elif re.fullmatch(r"\d+(?:\.\d+)?", headers.get("retry-after-ms", "")):
        result["retry_after"] = float(headers["retry-after-ms"]) / 1000
    elif hint := re.search(rf"(?:try again in|retry after)\s+((?:{DURATION_PART}\s*)+)", message, re.I):
        result["retry_after"] = duration_seconds(hint[1])
    else:
        # A reset header describes its whole bucket. Prefer a wait for this request.
        buckets = ["tokens", "project-tokens", "requests"]
        if metric:
            buckets = ["tokens", "project-tokens"] if result["metric"].startswith("T") else ["requests"]
        delays = {bucket: duration_seconds(headers["x-ratelimit-reset-" + bucket]) for bucket in buckets
                  if "x-ratelimit-reset-" + bucket in headers}
        exhausted = [delay for bucket, delay in delays.items()
                     if delay is not None and headers.get("x-ratelimit-remaining-" + bucket) == "0"]
        if exhausted:
            result["retry_after"] = max(exhausted)
        elif len(delays) == 1:
            result["retry_after"] = next(iter(delays.values()))
    return result


def explain(event, status=0, request_id=None, response_id=None):
    response = event.get("response") or {}
    err = response.get("error") or event.get("error") or event
    status = event.get("status") if isinstance(event.get("status"), int) else status
    code = error_code(err)
    message = err.get("message")
    if code == "error":
        code = ""
    if message == "error":
        message = None
    if status == 401 or code == "invalid_api_key":
        note = "This API key is invalid or has been revoked. Update the local key, then regenerate the response."
    elif code in ("insufficient_quota", "credit_balance_exhausted", "billing_hard_limit_reached"):
        note = "API credits exhausted. Check the balance, expiry, and organization in OpenAI Billing."
    elif code in SPEND_LIMIT_CODES:
        note = "The organization or project has reached its usage or spending limit. Check the limits in OpenAI Billing."
    elif temporary_rate_limit(err, status, event.get("headers") or {}):
        note = "The rate limit was reached. Wait a moment before retrying."
    elif code == "model_not_found" or (status in (403, 404) and not message):
        note = "This model or resource is unavailable to the project. Choose an available Responses model and check the key permissions."
    elif code == "context_length_exceeded":
        note = "The request exceeds the model context limit. Include fewer messages or shorten the attachment."
    elif status >= 500:
        note = "OpenAI is temporarily unavailable. Try again later."
    else:
        detail = message or code
        note = "OpenAI could not complete the request" + (": " + str(detail) if detail else ".")
        message = None
    request_id = event.get("request_id") or err.get("request_id") or (event.get("headers") or {}).get("x-request-id") or request_id
    response_id = response.get("id") or event.get("response_id") or response_id
    identifiers = [f"HTTP {status}" if status else "", code, f"param={err['param']}" if err.get("param") else "",
                   f"request_id={request_id}" if request_id else "", f"response_id={response_id}" if response_id else ""]
    parts = [note, message, " · ".join(str(item) for item in identifiers if item)]
    return "\n".join(re.sub(r"\b(?:sk|org)-[\w*-]+", "[redacted]", str(part))[:600] for part in parts if part)


def failure_details(event, status=0, request_id=None, response_id=None, multi_agent=False):
    response = event.get("response") or event
    result = response_details(response, multi_agent)
    if result["usage"] is None:
        result["usage"] = event.get("usage") or (event.get("error") or {}).get("usage")
    result.update(status="error", note=explain(event, status, request_id, response_id))
    err = response.get("error") or event.get("error") or event
    status = event.get("status") if isinstance(event.get("status"), int) else status
    limit = rate_limit_info(err, status, event.get("headers") or {})
    if limit:
        result["rate_limit"] = limit
    return result


def http_failure(payload, status, headers, multi_agent):
    try:
        event = json.loads(payload or "{}")
    except ValueError:
        event = {}
    if headers:
        event["headers"] = {name.lower(): value for name, value in headers.items()}
    result = failure_details(event, status, multi_agent=multi_agent)
    rejected = 400 <= status < 500 and result["usage"] is None
    if result["usage"] is not None or "rate_limit" in result:
        if rejected:
            result["rejected"] = True
        return "end", result
    return ("rejected" if rejected else "error"), result["note"]


class Decoder:
    """Decode one Responses segment identically on SSE and WebSocket transports."""
    def __init__(self, body):
        self.multi_agent = bool(body.get("multi_agent"))
        self.keep_items = (any(tool["type"] == "function" for tool in body.get("tools", []))
                           or getattr(body, "new_checkpoint", False)
                           or body["input"][-1].get("type") == "compaction_trigger")
        self.completed_items = []
        self.text_parts = []
        self.request_id = None
        self.response_id = None
        self.accepted = False
        self.hidden_outputs = set()
        self.subagents = set()
        self.agents = agent_activity.AgentStream() if self.multi_agent else None

    def feed(self, e):
        kind = e.get("type")
        if kind and kind.startswith("response."):
            self.accepted = True
            self.response_id = (e.get("response") or {}).get("id") or self.response_id
        item = e.get("item") or {}
        agent = (e.get("agent") or item.get("agent") or {}).get("agent_name", "/root")
        index = e.get("output_index")
        if self.agents:
            agent, item_id = self.agents.route(e)
            if item and agent != "/root" and not item.get("agent"):
                item = e["item"] = item | {"agent": {"agent_name": agent}}
            for update in self.agents.updates(e, agent, item_id):
                name = update["name"]
                if name not in self.subagents:
                    self.subagents.add(name)
                    yield "subagent", name
                yield "agent_activity", update
            # Some transports attach ownership only to the streamed item event.
            for i, final_item in enumerate((e.get("response") or {}).get("output") or []):
                owner = self.agents.owners.get(final_item.get("id") or self.agents.items.get(i), "/root")
                if owner != "/root" and not final_item.get("agent"):
                    final_item["agent"] = {"agent_name": owner}
        if kind == "response.output_item.done":
            self.completed_items.append(item)
        if agent != "/root" and agent not in self.subagents:
            self.subagents.add(agent)
            yield "subagent", agent
        if agent != "/root" and kind.startswith("response.") and not e.get("response"):
            return
        if self.multi_agent and kind == "response.output_item.added" and (
            agent != "/root" or item.get("phase") == "commentary"
        ):
            self.hidden_outputs.add(index)
        if kind in ("response.output_text.delta", "response.refusal.delta"):
            if agent == "/root" and index not in self.hidden_outputs:
                self.text_parts.append(e["delta"])
                yield "delta", e["delta"]
        elif kind in ("response.web_search_call.in_progress", "response.web_search_call.searching"):
            yield "search", "searching"
        elif kind == "response.web_search_call.completed":
            yield "search", "completed"
        elif kind == "response.output_item.added" and e["item"]["type"] == "reasoning":
            yield "reasoning", None
        elif kind == "response.reasoning_summary_text.delta" and agent == "/root":
            yield "summary", e["delta"]
        elif kind == "response.output_item.added" and item.get("type") == "compaction":
            yield "compacting", None
        elif kind in ("response.completed", "response.incomplete"):
            response = e["response"]
            if not response.get("output"):
                response["output"] = self.completed_items
            result = response_details(response, self.multi_agent)
            items = response.get("output") or []
            if self.keep_items:
                result["output_items"] = items
            elif compacted := context.latest_compaction(items):
                result["output_items"] = compacted
            if self.subagents:
                result["subagents"] = sorted(self.subagents.union(result.get("subagents", [])))
            result.update(status="done", note="")
            if kind == "response.incomplete":
                reason = (response.get("incomplete_details") or {}).get("reason") or ""
                result.update(status="incomplete", reason=reason)
                if reason == "max_output_tokens":
                    result["note"] = ("The response reached its output limit." if result.get("text") else
                                      "The model used its output budget during reasoning. Try a lower Effort or a shorter prompt.")
                elif reason == "content_filter":
                    result["note"] = "The response was cut short by OpenAI's content filter."
                else:
                    result["note"] = "OpenAI ended the response before it completed."
            yield "end", result
            return
        elif kind in ("response.failed", "error"):
            result = failure_details(e, request_id=self.request_id, response_id=self.response_id, multi_agent=self.multi_agent)
            if not result.get("text") and self.text_parts:
                result["text"] = "".join(self.text_parts)
            if self.completed_items and not (e.get("response") or e).get("output"):
                result["search_calls"] = max(result["search_calls"],
                    response_details({"output": self.completed_items}, self.multi_agent)["search_calls"])
            rejected = not self.accepted and 400 <= (e.get("status") or 0) < 500 and result["usage"] is None
            if rejected and "rate_limit" not in result:
                yield "rejected", result["note"]
            else:
                if rejected:
                    result["rejected"] = True
                yield "end", result
            return


def events(key, body, stopped):
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept": "text/event-stream"}
    multi_agent = bool(body.get("multi_agent"))
    if multi_agent:
        headers["OpenAI-Beta"] = "responses_multi_agent=v1"
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), headers=headers)
    decoder = Decoder(body)
    try:
        with urllib.request.urlopen(req, timeout=900 if body["service_tier"] == "flex" else 120) as response:
            decoder.request_id = (getattr(response, "headers", None) or {}).get("x-request-id")
            for line in response:
                if stopped.is_set():
                    return
                if not line.startswith(b"data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == b"[DONE]":
                    continue
                e = json.loads(raw)
                for kind, value in decoder.feed(e):
                    yield kind, value
                    if kind in ("end", "error", "rejected"):
                        return
        if not stopped.is_set():
            yield "error", "The connection ended early. The partial response is available to copy or regenerate."
    except urllib.error.HTTPError as exc:
        yield http_failure(exc.read(), exc.code, exc.headers, multi_agent)
    except (OSError, ValueError):
        yield "error", "Could not read the OpenAI response. Check the network or HTTPS_PROXY setting, then retry."
