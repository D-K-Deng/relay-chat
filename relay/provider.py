"""Translate local chat messages to a stateless OpenAI Responses request."""
import json
import re
import urllib.error
import urllib.request

ENDPOINT = "https://api.openai.com/v1/responses"


def user_message(data):
    text = data.get("text", "")
    attachments = data.get("files") or []
    if not isinstance(text, str) or len(text) > 500_000 or not isinstance(attachments, list) or len(attachments) > 4:
        raise ValueError("Shorten the message or attach no more than four files.")
    files = []
    for item in attachments:
        kind, value = item.get("kind"), item.get("data", "")
        if not isinstance(value, str):
            raise ValueError("Could not read this attachment.")
        if kind == "image":
            if len(value) > 4_200_000 or not re.match(r"^data:image/(png|jpeg|webp|gif);base64,", value):
                raise ValueError("Choose a PNG, JPEG, WebP, or GIF image up to 3 MB.")
        elif kind != "text" or len(value) > 100_000:
            raise ValueError("Text attachments can contain up to 100,000 characters each.")
        files.append({"name": str(item.get("name", "Attachment"))[:200], "kind": kind, "data": value})
    if not text.strip() and not files:
        raise ValueError("Enter a message or add an attachment.")
    return {"role": "user", "text": text.strip(), "files": files}


def request_body(history, settings):
    usable = [m for m in history if m["role"] in ("user", "assistant") and (m["text"] or m.get("files"))]
    take = settings["context"]
    selected = usable[-take:] if take else usable
    data = []
    for m in selected:
        if m["role"] == "assistant":
            data.append({"role": "assistant", "content": m["text"]})
            continue
        parts = [{"type": "input_text", "text": m["text"]}] if m["text"] else []
        for f in m.get("files", []):
            if f["kind"] == "image":
                parts.append({"type": "input_image", "image_url": f["data"], "detail": "auto"})
            else:
                parts.append({"type": "input_text", "text": f"Attachment: {f['name']}\n\n{f['data']}"})
        data.append({"role": "user", "content": parts})
    body = {"model": settings["model"], "input": data, "stream": True, "store": False,
            "max_output_tokens": settings["max_output"]}
    body["instructions"] = settings["instructions"].strip() or "Reply in English unless the user requests another language."
    return body


def explain(err, status=0):
    code = err.get("code") or err.get("type") or ""
    if status == 401 or code == "invalid_api_key":
        return "This API key is invalid or has been revoked. Update the local key, then regenerate the response."
    if code in ("insufficient_quota", "credit_balance_exhausted", "billing_hard_limit_reached"):
        return "API credits exhausted. Check the balance, expiry, and organization in OpenAI Billing."
    if status == 429 or code == "rate_limit_exceeded":
        return "The rate limit was reached. Wait a moment before retrying."
    if status in (403, 404) or code == "model_not_found":
        return "This model or resource is unavailable to the project. Choose an available Responses model and check the key permissions."
    if code == "context_length_exceeded":
        return "The request exceeds the model context limit. Include fewer messages or shorten the attachment."
    if status >= 500:
        return "OpenAI is temporarily unavailable. Try again later."
    detail = re.sub(r"sk-[\w*-]+", "[redacted]", str(err.get("message") or code))[:450]
    return "OpenAI could not complete the request" + (": " + detail if detail else ".")


def events(key, body, stopped):
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept": "text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            for line in response:
                if stopped.is_set():
                    return
                if not line.startswith(b"data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == b"[DONE]":
                    continue
                e = json.loads(raw)
                kind = e.get("type")
                if kind in ("response.output_text.delta", "response.refusal.delta"):
                    yield "delta", e["delta"]
                elif kind in ("response.completed", "response.incomplete"):
                    obj = e["response"]
                    reason = (obj.get("incomplete_details") or {}).get("reason")
                    note = ("The response reached the output limit. Send a follow-up to continue." if reason == "max_output_tokens" else
                            "The response is incomplete: " + str(reason or "unknown reason")) if kind.endswith("incomplete") else ""
                    yield "end", {"status": "incomplete" if note else "done", "usage": obj.get("usage"), "note": note,
                                  "billed_model": obj.get("model"), "service_tier": obj.get("service_tier", "default")}
                    return
                elif kind in ("response.failed", "error"):
                    yield "error", explain((e.get("response") or {}).get("error") or e)
                    return
        if not stopped.is_set():
            yield "error", "The connection ended early. The partial response is available to copy or regenerate."
    except urllib.error.HTTPError as exc:
        try:
            err = json.loads(exc.read()).get("error", {})
        except ValueError:
            err = {}
        yield ("rejected" if 400 <= exc.code < 500 else "error"), explain(err, exc.code)
    except (OSError, ValueError):
        yield "error", "Could not read the OpenAI response. Check the network or HTTPS_PROXY setting, then retry."
