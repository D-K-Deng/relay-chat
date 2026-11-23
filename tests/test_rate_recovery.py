import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from relay import harness, live, provider
from relay.project_files import ProjectFiles
from relay.storage import DEFAULTS
from test_live_context import SocketFixture, complete, answer


MODEL = "gpt-6.2-astra"
SETTINGS = DEFAULTS | {"model": MODEL, "effort": "ultra", "service_tier": "ultrafast"}
LIMIT = {"status": "error", "rate_limit": {"metric": "TPM", "retry_after": .169}, "note": "TPM limit"}


def task():
    return provider.request_body([provider.user_message({"text": "Create the report and explain it."})], SETTINGS)


class RateRecoveryTests(unittest.TestCase):
    def test_ultra_recovers_after_tool_write_and_worker_output_using_text_only_https(self):
        worker = {"agent_name": "/root/reviewer"}
        write = {"type": "function_call", "call_id": "write", "name": "create_file",
                 "arguments": json.dumps({"path": "note.txt", "content": "Only once"})}
        error = {"type": "error", "status": 429, "error": {"code": "rate_limit_exceeded",
                 "message": "Rate limit reached on tokens per min (TPM). Please try again in 169ms."}}
        def ws(event):
            if event["type"] == "response.inject":
                return [{"type": "response.inject.created"}, error]
            return [{"type": "response.created", "response": {"id": "resp_fixture"}},
                    {"type": "response.output_item.added", "output_index": 0, "item": {
                        "type": "message", "id": "child", "phase": "commentary", "content": [], "agent": worker}},
                    {"type": "response.output_text.delta", "output_index": 0, "delta": "The chart is ready."},
                    {"type": "response.output_text.delta", "output_index": 1, "delta": "Partial reply."},
                    {"type": "response.output_item.done", "output_index": 2, "item": write}]
        sock = SocketFixture(ws)
        stop = live.RunControl(MODEL)
        encoded_error = json.dumps({"error": error["error"]}).encode()
        rejected = HTTPError(provider.ENDPOINT, 429, "Rate limited", {}, io.BytesIO(encoded_error))
        suffix = answer("reply. Done.")
        suffix["content"][0]["annotations"] = [{"type": "url_citation", "url": "https://example.com/",
                                                "title": "Result", "start_index": 7, "end_index": 11}]
        events = [{"type": "response.output_text.delta", "delta": "rep"},
                  {"type": "response.output_text.delta", "delta": "ly. Done."}, complete([suffix])]
        stream = io.BytesIO("".join("data: " + json.dumps(e) + "\n\n" for e in events).encode())
        recorded, emitted = [], []
        with tempfile.TemporaryDirectory() as root:
            project = ProjectFiles(Path(root).resolve(), "edit", stop, threading.Lock())
            turn = harness.Turn(stop, project)
            with patch.object(stop, "_connect", return_value=sock), patch.object(stop, "wait", return_value=False), \
                    patch("urllib.request.urlopen", side_effect=[rejected, stream]) as https:
                turn.send(provider.events, "fixture", task(), lambda event, **v: emitted.append((event, v)),
                          lambda _: True, recorded.append)
            self.assertEqual((Path(root) / "note.txt").read_text(), "Only once")
        self.assertEqual([event["type"] for event in sock.sent], ["response.create", "response.inject"])
        self.assertEqual(len(turn.activity), 1)
        self.assertEqual(https.call_count, 2)
        first, second = (json.loads(call.args[0].data) for call in https.call_args_list)
        self.assertEqual(first, second)
        for key in ("model", "reasoning", "service_tier"):
            self.assertEqual(first[key], sock.sent[0][key])
        self.assertNotIn("tools", first)
        self.assertNotIn("multi_agent", first)
        self.assertNotIn("previous_response_id", first)
        progress = json.loads(next(i["content"] for i in first["input"] if i["role"] == "user" and isinstance(i["content"], str)))
        self.assertEqual(progress["worker_messages"], {"/root/reviewer": ["The chart is ready."]})
        self.assertEqual([action["tool"] for action in progress["tool_results"]], ["create_file"])
        self.assertEqual([r["status"] for r in recorded], ["error", "error", "done"])
        self.assertTrue(recorded[0]["usage_estimated"])
        self.assertTrue(recorded[1]["rejected"])
        self.assertIsNone(recorded[1]["usage"])
        self.assertEqual([r["service_tier"] for r in recorded], ["ultrafast"] * 3)
        result = turn.result()
        self.assertEqual((result["status"], result["text"]), ("done", "Partial reply. Done."))
        self.assertIn("used no tools", result["note"])
        self.assertEqual("".join(v["text"] for k, v in emitted if k == "delta"), result["text"])
        self.assertEqual(result["usage"]["input_tokens"], recorded[0]["usage"]["input_tokens"] + 6)
        self.assertEqual(result["usage"]["output_tokens"], recorded[0]["usage"]["output_tokens"] + 2)
        self.assertEqual(result["subagents"], ["/root/reviewer"])
        source, = result["sources"]
        self.assertEqual(result["text"][source["start_index"]:source["end_index"]], "Done")

    def test_confirmed_preflight_retry_preserves_full_request(self):
        requests, recorded = [], []
        def upstream(key, body, stopped):
            requests.append(json.dumps(body))
            if len(requests) == 1:
                yield "end", LIMIT | {"rejected": True}
            else:
                yield "end", {"status": "done", "text": "Done."}
        stop = threading.Event()
        turn = harness.Turn(stop)
        with patch.object(stop, "wait", return_value=False):
            turn.send(upstream, "fixture", task(), lambda *a, **kw: None, None, recorded.append)
        self.assertEqual(requests, [requests[0]] * 2)
        self.assertIn("multi_agent", json.loads(requests[1]))
        self.assertEqual(turn.result()["status"], "done")

    def test_stopping_during_wait_keeps_partial_answer(self):
        stop = threading.Event()
        turn = harness.Turn(stop)
        calls = []
        def upstream(key, body, stopped):
            calls.append(body)
            yield "delta", "Kept partial text"
            yield "end", dict(LIMIT)
        def emit(kind, **data):
            if kind == "rate_wait":
                stop.set()
        turn.send(upstream, "fixture", task(), emit, None, lambda _: None)
        self.assertEqual(len(calls), 1)
        self.assertEqual((turn.result()["status"], turn.result()["text"]), ("stopped", "Kept partial text"))

    def test_retry_only_resends_the_rejected_tool_handoff(self):
        requests = []
        write = {"type": "function_call", "call_id": "writing", "name": "create_file",
                 "arguments": json.dumps({"path": "handoff.txt", "content": "Created once"})}
        def upstream(key, body, stopped):
            requests.append(json.loads(json.dumps(body)))
            if len(requests) == 1:
                yield "end", {"status": "done", "output_items": [write]}
            elif len(requests) == 2:
                yield "end", LIMIT | {"rejected": True}
            else:
                yield "end", {"status": "done", "text": "Done."}
        with tempfile.TemporaryDirectory() as root:
            stop = threading.Event()
            project = ProjectFiles(Path(root).resolve(), "edit", stop, threading.Lock())
            turn = harness.Turn(stop, project)
            with patch.object(stop, "wait", return_value=False):
                turn.send(upstream, "fixture", task(), lambda *a, **kw: None, lambda _: True, lambda _: None)
            self.assertEqual((Path(root) / "handoff.txt").read_text(), "Created once")
        self.assertEqual(len(turn.activity), 1)
        self.assertEqual(len(requests), 3)
        self.assertEqual(requests[1], requests[2])
        self.assertIn("tools", requests[2])
        self.assertEqual(requests[2]["input"][-1]["call_id"], "writing")

    def test_created_but_empty_response_can_retry_the_same_request(self):
        requests = []
        stop = threading.Event()
        turn = harness.Turn(stop)
        def upstream(key, body, stopped):
            requests.append(json.dumps(body))
            if len(requests) == 1:
                decoder = provider.Decoder(body)
                yield from decoder.feed({"type": "response.created", "response": {"id": "empty"}})
                yield from decoder.feed({"type": "response.output_item.added", "item": {"type": "reasoning"}})
                yield from decoder.feed({"type": "error", "status": 429,
                                         "error": {"code": "rate_limit_exceeded", "message": "TPM limit"}})
            else:
                yield "end", {"status": "done", "text": "Success"}
        with patch.object(stop, "wait", return_value=False):
            turn.send(upstream, "fixture", task(), lambda *a, **kw: None, None, lambda _: None)
        self.assertEqual(requests, [requests[0]] * 2)
        self.assertEqual(turn.result()["text"], "Success")

    def test_incremental_recovery_reuses_empty_attempts_then_advances_the_partial(self):
        requests, waits, visible = [], [], []
        def upstream(key, body, stopped):
            requests.append(json.loads(json.dumps(body)))
            attempt = len(requests)
            if attempt == 8:
                yield "delta", "detail. Done."
                yield "end", {"status": "done", "text": "detail. Done."}
                return
            if attempt == 1:
                yield "delta", "First item."
            elif attempt == 6:
                yield "delta", "item. More"
            elif attempt == 7:
                yield "delta", "More detail."
            yield "end", dict(LIMIT)
        def emit(kind, **data):
            if kind == "rate_wait":
                waits.append(data["attempt"])
            if kind == "delta":
                visible.append(data["text"])
        stop = threading.Event()
        turn = harness.Turn(stop)
        with patch.object(stop, "wait", return_value=False):
            turn.send(upstream, "fixture", task(), emit, None, lambda _: None)
        self.assertEqual(waits, list(range(1, 8)))
        self.assertEqual(requests[1:6], [requests[1]] * 5)
        for request, partial in zip(requests[6:], ["First item. More", "First item. More detail."]):
            history = [message["content"] for message in request["input"] if message.get("role") == "assistant"]
            self.assertEqual(history, [partial])
            self.assertNotIn("tools", request)
        self.assertEqual(turn.result()["text"], "First item. More detail. Done.")
        self.assertEqual("".join(visible), turn.result()["text"])
        self.assertFalse(turn.result().get("note"))
        self.assertEqual(harness.overlap("hello", "ordinary"), 0)

    def test_disconnect_during_cooldown_prevents_another_request(self):
        stop = threading.Event()
        turn = harness.Turn(stop)
        calls = []
        def upstream(key, body, stopped):
            calls.append(body)
            yield "delta", "Preserved."
            yield "end", LIMIT | {"rate_limit": {"retry_after": 30}}
        def emit(kind, **data):
            if kind == "ping":
                raise ConnectionResetError("fixture client disconnected")
        with patch.object(stop, "wait", return_value=False), self.assertRaises(ConnectionResetError):
            turn.send(upstream, "fixture", task(), emit, None, lambda _: None)
        self.assertEqual(len(calls), 1)
        self.assertEqual(turn.result()["text"], "Preserved.")

    def test_interrupted_auto_review_discards_partial_json_before_retry(self):
        reviews, charged = [], []
        write = {"type": "function_call", "call_id": "write", "name": "create_file",
                 "arguments": json.dumps({"path": "report.txt", "content": "Approved once"})}
        def upstream(key, body, stopped):
            if body.get("text", {}).get("format", {}).get("name") == "workspace_review":
                reviews.append(body)
                if len(reviews) == 1:
                    yield "delta", '{"decision":"man'
                    yield "end", dict(LIMIT)
                else:
                    yield "delta", '{"decision":"approve","reason":"Requested report"}'
                    yield "end", {"status": "done"}
            elif body["input"][-1].get("type") == "function_call_output":
                yield "end", {"status": "done", "text": "Written"}
            else:
                yield "end", {"status": "done", "output_items": [write]}
        with tempfile.TemporaryDirectory() as root:
            stop = threading.Event()
            project = ProjectFiles(Path(root).resolve(), "edit", stop, threading.Lock(), auto_review=True)
            turn = harness.Turn(stop, project)
            with patch.object(stop, "wait", return_value=False):
                turn.send(upstream, "fixture", task(), lambda *a, **kw: None,
                          lambda _: self.fail("Partial JSON leaked into the next review"), charged.append)
            self.assertEqual((Path(root) / "report.txt").read_text(), "Approved once")
        self.assertEqual(reviews, [reviews[0]] * 2)
        self.assertEqual([r["status"] for r in charged], ["done", "error", "done", "done"])
