"""Mid-turn steering on a single Responses WebSocket for the lifetime of a turn."""
import json
import queue
import threading

from . import provider


def supports(model):
    return model.startswith(("gpt-6-", "gpt-6.")) or model == "gpt-6"


def prepend_output(result, text, sources):
    if text or sources:
        offset = len(text)
        result["sources"] = sources + [
            source | {"start_index": offset + source["start_index"], "end_index": offset + source["end_index"]}
            for source in result.get("sources", [])]
        result["text"] = text + result.get("text", "")


class RunControl(threading.Event):
    def __init__(self, model):
        super().__init__()
        self.model = model
        self.lock = threading.RLock()
        self.socket = None
        self.response_id = ""
        self.phase = "starting"
        self.updates = []
        self.pending = None
        self.fallback = False
        self.held_result = None

    def set(self):
        super().set()
        with self.lock:
            self.phase = "finished"
            sock, self.socket = self.socket, None
            if self.pending:
                self.pending.update(status="failed", note="The run ended before OpenAI confirmed this update.")
                self.pending = None
        if sock:
            sock.shutdown()

    def steer(self, text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Enter an update for this run.")
        text = text.strip()
        with self.lock:
            if self.phase not in {"generating", "tools"} or self.is_set():
                raise ValueError("This run is not accepting updates. Send your text as a new message.")
            if self.pending:
                raise ValueError("The previous update is queued. Wait for its confirmation before adding another.")
            update = {"text": text, "status": "queued"}
            if self.phase == "generating":
                try:
                    self.socket.send(json.dumps({"type": "response.steer", "previous_response_id": self.response_id,
                                                 "input": text}))
                except Exception as error:
                    raise ValueError("Could not send the update. Your draft can be sent again.") from error
            # During a local tool/approval the next response.create can take the
            # update after the required function_call_output, on this same socket.
            self.updates.append(update)
            self.pending = update
            return dict(update)

    def settle(self, status, note=""):
        update = self.pending
        if update:
            update["status"] = status
            if note:
                update["note"] = note
            self.pending = None
            return dict(update)

    def _connect(self, key, body):
        import websocket
        url = provider.ENDPOINT.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        headers = {"Authorization": "Bearer " + key}
        if body.get("multi_agent"):
            url += "?beta=true"
            headers["OpenAI-Beta"] = "responses_multi_agent=v1"
        sock = websocket.create_connection(url, header=headers, timeout=12, suppress_origin=True)
        sock.settimeout(900 if body.get("service_tier") == "flex" else 120)
        return sock

    def events(self, key, body, stopped):
        if not self.socket and not self.fallback:
            try:
                sock = self._connect(key, body)
                with self.lock:
                    if self.is_set():
                        sock.shutdown()
                        return
                    self.socket = sock
            except Exception as error:
                if getattr(error, "status_code", None) == 429:
                    yield provider.http_failure(error.resp_body, 429, error.resp_headers, bool(body.get("multi_agent")))
                    return
                # No response.create has been sent. An HTTPS retry is safe.
                self.fallback = True
                yield "steering", {"available": False, "note": "WebSocket unavailable. This reply uses HTTPS."}
        if self.fallback:
            yield from provider.events(key, body, stopped)
            return
        failure = None
        try:
            for kind, value in self._exchange(body):
                if kind == "resume_https":
                    self.socket.shutdown()
                    with self.lock:
                        self.fallback = True
                        self.phase = "starting"
                    yield "steering", {"available": False, "note": "WebSocket continuation expired. This reply uses HTTPS."}
                    for output, result in provider.events(key, body, stopped):
                        if self.pending and (output in {"delta", "reasoning", "search", "subagent"} or
                                             (output == "end" and result["status"] != "error")):
                            with self.lock:
                                update = self.settle("applied")
                            if update:
                                yield "steer", update
                        if output == "end":
                            prepend_output(result, value["text"], value["sources"])
                        yield output, result
                    return
                if kind in {"error", "rejected"} or (kind == "end" and value["status"] == "error"):
                    failure = (kind, value)
                    break
                yield kind, value
        except (OSError, ValueError, EOFError) as error:
            failure = ("error", "The live connection ended. Retry the response. " + str(error)[:160])
        except Exception:
            failure = ("error", "The live connection ended. Retry the response.")
        finally:
            with self.lock:
                if self.phase != "tools":
                    self.phase = "finished"
        if failure:
            kind, value = failure
            limited = value.get("rate_limit") if kind == "end" else None
            if self.held_result:
                note = value["note"] if kind == "end" else value
                failure = "end", self.held_result | {"status": "error", "note": note}
                if limited:
                    failure[1]["rate_limit"] = limited
                self.held_result = None
            if limited:
                # An error event can close this socket. A later attempt starts a
                # fresh response and cannot acknowledge steering from this one.
                with self.lock:
                    sock, self.socket = self.socket, None
                    pending = self.settle("failed", "The run ended before OpenAI confirmed this update.")
                if sock:
                    sock.shutdown()
                if pending:
                    yield "steer", pending
            yield failure

    def _exchange(self, body):
        request = {k: v for k, v in body.items() if k not in ("stream", "background")}
        request["type"] = "response.create"
        with self.lock:
            sock = self.socket
            continuing = self.phase == "tools"
            local_update = self.pending if continuing and self.pending and not self.native_pending else None
            if continuing:
                request["previous_response_id"] = self.response_id
                request["input"] = body["input"][self.output_boundary:]
                if local_update:
                    request["input"].append({"role": "user", "content": local_update["text"]})
                    body["input"].append(request["input"][-1])
            self.phase = "starting"
            sock.send(json.dumps(request))
        decoder = provider.Decoder(body)
        prior = None
        carried_items = []
        carried_text = ""
        carried_sources = []
        injections = {}
        awaiting_ack = 0
        completed = None
        retry_outputs = []
        reference_pending = continuing

        def carry(segment):
            nonlocal carried_text
            offset = len(carried_text)
            carried_sources.extend(source | {"start_index": offset + source["start_index"], "end_index": offset + source["end_index"]}
                                   for source in segment.get("sources", []))
            carried_text += segment.get("text", "")
            carried_items.extend(segment.get("output_items", []))

        while not self.is_set():
            raw = sock.recv()
            if not raw:
                raise EOFError("Connection closed.")
            event = json.loads(raw)
            kind = event.get("type")
            # An explicitly rejected reference has not started a new response.
            # The harness already has the tool calls and their executed results.
            if (reference_pending and not decoder.accepted and kind == "error"
                    and (event.get("error") or event).get("code") == "previous_response_not_found"):
                rejected = provider.failure_details(event)
                if rejected["usage"] is not None:
                    yield "end", rejected
                    return
                yield "segment", {"status": "rejected", "usage": None}
                if prior:
                    carry(prior)
                    body["input"].extend(carried_items)
                if self.pending and self.pending is not local_update:
                    body["input"].append({"role": "user", "content": self.pending["text"]})
                yield "resume_https", {"text": carried_text, "sources": carried_sources}
                return
            if body.get("multi_agent") and kind == "response.output_item.done" and event["item"].get("type") == "function_call":
                # Display the worker's tool request before pausing for local approval.
                yield from decoder.feed(event)
                call = event["item"]
                answer = queue.Queue()
                yield "inline_call", (call, answer)
                while not self.is_set():
                    try:
                        output = answer.get(timeout=.45)
                        break
                    except queue.Empty:
                        continue
                else:
                    return
                injections[call["call_id"]] = output
                sock.send(json.dumps({"type": "response.inject", "response_id": self.response_id, "input": [output]}))
                awaiting_ack += 1
                continue
            elif kind in ("response.inject.created", "response.inject.failed"):
                awaiting_ack -= 1
                if kind == "response.inject.failed":
                    if event["error"].get("code") != "response_already_completed":
                        failure = provider.failure_details(event, response_id=self.response_id, multi_agent=True)
                        yield "end", failure
                        return
                    retry_outputs.extend(event["input"])
                if awaiting_ack or not completed:
                    continue
                event, completed = completed, None
                self.held_result = None
                kind = event["type"]
            elif awaiting_ack and kind in ("response.completed", "response.incomplete"):
                completed = event
                self.held_result = provider.response_details(event["response"], decoder.multi_agent)
                continue
            if kind == "response.created":
                reference_pending = False
                with self.lock:
                    applied = self.settle("applied") if prior or continuing else None
                    self.response_id = event["response"]["id"]
                    self.native_pending = False
                    self.phase = "generating"
                if prior:
                    if self.held_result:
                        self.held_result = None
                        yield "segment", prior
                    carry(prior)
                    if applied:
                        carried_items.append({"role": "user", "content": applied["text"]})
                    decoder = provider.Decoder(body)
                    prior = None
                decoder.accepted = True
                decoder.response_id = self.response_id
                if applied:
                    yield "steer", applied
                yield "steering", {"available": True}
                continuing = False
                continue
            if kind == "response.steer.accepted":
                with self.lock:
                    self.native_pending = True
                continue
            if kind == "response.steer.failed":
                with self.lock:
                    self.native_pending = False
                    failed = self.settle("failed", provider.explain(event, response_id=self.response_id))
                if failed:
                    yield "steer", failed
                if prior and self.held_result:
                    with self.lock:
                        self.phase = "finished"
                    self.held_result = None
                    yield "end", prior
                    return
                continue
            for output, value in decoder.feed(event):
                if output != "end":
                    yield output, value
                    if output in {"error", "rejected"}:
                        return
                    continue
                if value["status"] == "error":
                    prepend_output(value, carried_text, carried_sources)
                    yield "end", value
                    return
                items = value.get("output_items", [])
                if injections:
                    present = {i.get("call_id") for i in items if i.get("type") == "function_call_output"}
                    replay = []
                    for item in items:
                        replay.append(item)
                        call_id = item.get("call_id")
                        if item.get("type") == "function_call" and call_id in injections and call_id not in present:
                            replay.append(injections[call_id])
                    items = value["output_items"] = replay
                if retry_outputs:
                    prior = value
                    yield "segment", prior
                    reference_pending = True
                    decoder = provider.Decoder(body)
                    sock.send(json.dumps({**request, "previous_response_id": self.response_id, "input": retry_outputs}))
                    retry_outputs = []
                    continue
                calls = any(i.get("type") == "function_call" and i.get("call_id") not in injections for i in items)
                with self.lock:
                    if calls and value.get("status") in {"done", "incomplete"} and value.get("reason") in (None, "steered"):
                        self.phase = "tools"
                        # Treat a tool-bearing steer as a normal tool handoff. The
                        # server applies queued steering when the outputs arrive.
                        if self.pending:
                            self.native_pending = True
                        value.update(status="done", note="")
                        value.pop("reason", None)
                        self.output_boundary = len(body["input"]) + len(carried_items) + len(items)
                    elif self.pending and value.get("status") != "error":
                        self.phase = "continuing"
                        prior = value
                        self.held_result = prior
                        continue
                    else:
                        self.phase = "finished"
                if carried_items:
                    value["output_items"] = carried_items + items
                prepend_output(value, carried_text, carried_sources)
                if injections:
                    value["executed_calls"] = list(injections)
                yield "end", value
                return
