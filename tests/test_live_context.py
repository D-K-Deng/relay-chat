import json
import io
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from relay import harness, live, provider
from relay.project_files import ProjectFiles
from relay.storage import DEFAULTS, Store


MODEL = "gpt-6.2-sol"
SETTINGS = DEFAULTS | {"model": MODEL}


def message(role, text):
    return {"role": role, "text": text}


def answer(text):
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}


def complete(items, tokens=6):
    return {"type": "response.completed", "response": {"output": items, "usage": {"input_tokens": tokens, "output_tokens": 2}}}


class SocketFixture:
    def __init__(self, handler):
        self.handle = handler
        self.inbox = queue.Queue()
        self.sent = []

    def send(self, payload):
        event = json.loads(payload)
        self.sent.append(event)
        for result in self.handle(event):
            self.inbox.put(json.dumps(result))

    def recv(self):
        return self.inbox.get(timeout=3)

    def shutdown(self):
        self.inbox.put("")


class LiveRuns(unittest.TestCase):
    def run_live(self, handler, on_event=None, review=None, project=None, **settings):
        control = live.RunControl(MODEL)
        turn = harness.Turn(control, project)
        sock = SocketFixture(handler)
        recorded = []
        body = provider.request_body([message("user", "Do the task")], SETTINGS | settings)
        def emit(kind, **data):
            if on_event:
                on_event(control, kind, data)
        with patch.object(control, "_connect", return_value=sock):
            try:
                turn.send(provider.events, "fixture", body, emit, lambda proposal: review(control, proposal),
                          lambda result: recorded.append(dict(result)))
            finally:
                control.set()
        return turn.result(), sock.sent, recorded

    def test_mid_turn_steer_uses_automatic_continuation_and_accounts_for_each_response(self):
        def upstream(event):
            if event["type"] == "response.create":
                return [{"type": "response.created", "response": {"id": "first"}}]
            self.assertEqual(event, {"type": "response.steer", "previous_response_id": "first", "input": "Only Mac"})
            return [{"type": "response.steer.accepted", "steer": {"id": "s"}},
                    {"type": "response.incomplete", "response": {"output": [answer("Initial. ")],
                        "usage": {"input_tokens": 10, "output_tokens": 2}, "incomplete_details": {"reason": "steered"}}},
                    {"type": "response.created", "response": {"id": "second"}}, complete([answer("Mac only.")])]
        def steer(control, kind, data):
            if kind == "steering" and not control.updates:
                self.assertEqual(control.steer("Only Mac")["status"], "queued")
        result, sent, charged = self.run_live(upstream, steer)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["text"], "Initial. Mac only.")
        self.assertEqual(result["steers"], [{"text": "Only Mac", "status": "applied"}])
        self.assertEqual(result["usage"], {"input_tokens": 16, "output_tokens": 4})
        self.assertEqual((len(sent), len(charged)), (2, 2))
        self.assertNotIn("stream", sent[0])
        self.assertIs(sent[0]["store"], False)

    def test_steer_while_waiting_for_file_approval_is_sent_after_result(self):
        call = {"type": "function_call", "name": "create_file", "call_id": "write",
                "arguments": json.dumps({"path": "hello.txt", "content": "Hello"})}
        def upstream(event):
            if "previous_response_id" not in event:
                return [{"type": "response.created", "response": {"id": "first"}}, complete([call])]
            self.assertEqual(event["previous_response_id"], "first")
            self.assertEqual([item.get("type") or item["role"] for item in event["input"]], ["function_call_output", "user"])
            self.assertEqual(event["input"][1]["content"], "No file changes")
            return [{"type": "response.created", "response": {"id": "next"}}, complete([answer("Kept unchanged.")])]
        def decline(control, proposal):
            control.steer("No file changes")
            return False
        with tempfile.TemporaryDirectory() as folder:
            project = ProjectFiles(folder, "edit", threading.Event(), threading.Lock())
            result, sent, _ = self.run_live(upstream, project=project, review=decline)
            self.assertFalse((Path(folder) / "hello.txt").exists())
        self.assertEqual(result["steers"][0]["status"], "applied")
        self.assertEqual([evt["type"] for evt in sent], ["response.create", "response.create"])
        self.assertEqual(result["workspace_activity"][0]["status"], "denied")

    def test_ultra_injects_tool_result_once_even_if_response_completed_before_ack(self):
        call = {"type": "function_call", "call_id": "read", "name": "read_file",
                "arguments": json.dumps({"path": "notes.txt", "start": 1, "count": 1})}
        def upstream(event):
            if event["type"] == "response.inject":
                self.assertIn("fixture text", event["input"][0]["output"])
                return [{"type": "response.inject.failed", "error": {"code": "response_already_completed"},
                         "input": event["input"]}]
            if event.get("previous_response_id") == "first":
                self.assertEqual(event["input"][0]["call_id"], "read")
                return [{"type": "response.created", "response": {"id": "next"}}, complete([answer("Read it.")])]
            return [{"type": "response.created", "response": {"id": "first"}},
                    {"type": "response.output_item.done", "item": call}, complete([call])]
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "notes.txt").write_text("fixture text")
            project = ProjectFiles(folder, "read", threading.Event(), threading.Lock())
            result, sent, charged = self.run_live(upstream, project=project, effort="ultra")
        self.assertEqual(result["text"], "Read it.")
        self.assertEqual(len(result["workspace_activity"]), 1)
        self.assertEqual([evt["type"] for evt in sent], ["response.create", "response.inject", "response.create"])
        self.assertEqual(len(charged), 2)

    def test_accepted_response_error_preserves_partial_reply_and_provider_diagnostics(self):
        err = {"type": "error", "status": 400, "headers": {"x-request-id": "req_fixture"},
               "error": {"type": "invalid_request_error", "code": "invalid_value", "param": "input",
                         "message": "Invalid tool result."}, "usage": {"input_tokens": 41, "output_tokens": 7}}
        def upstream(event):
            return [{"type": "response.created", "response": {"id": "resp_fixture"}},
                    {"type": "response.output_text.delta", "delta": "Partial reply."}, err]
        result, sent, charges = self.run_live(upstream, effort="ultra", service_tier="ultrafast")
        self.assertEqual((result["text"], result["status"]), ("Partial reply.", "error"))
        for detail in ("Invalid tool result.", "HTTP 400", "invalid_value", "param=input", "req_fixture", "resp_fixture"):
            self.assertIn(detail, result["note"])
        self.assertEqual(result["usage"], err["usage"])
        self.assertEqual((len(sent), len(charges)), (1, 1))
        self.assertEqual(charges[0]["service_tier"], "ultrafast")

    def test_failed_injection_after_completion_keeps_its_usage_once(self):
        call = {"type": "function_call", "call_id": "list", "name": "find_files",
                "arguments": json.dumps({"path": ".", "pattern": "*"})}
        def upstream(event):
            if event["type"] == "response.inject":
                return [{"type": "response.inject.failed", "error": {"code": "invalid_value", "message": "Invalid result."}}]
            return [{"type": "response.created", "response": {"id": "first"}},
                    {"type": "response.output_item.done", "item": call}, complete([call], tokens=36)]
        with tempfile.TemporaryDirectory() as folder:
            project = ProjectFiles(folder, "read", threading.Event(), threading.Lock())
            result, _, charges = self.run_live(upstream, project=project, effort="ultra")
        self.assertEqual(result["status"], "error")
        self.assertIn("Invalid result.", result["note"])
        self.assertEqual(len(charges), 1)
        self.assertEqual(result["usage"], {"input_tokens": 36, "output_tokens": 2})

    def test_expired_reference_resumes_from_tool_history_without_repeating_file_write(self):
        call = {"type": "function_call", "call_id": "write", "name": "create_file",
                "arguments": json.dumps({"path": "new.txt", "content": "Created"})}
        lost = {"type": "error", "status": 400, "error": {"code": "previous_response_not_found",
                "type": "invalid_request_error", "message": "Previous response not found.", "param": "previous_response_id"}}
        def upstream(event):
            if event["type"] == "response.inject":
                return [{"type": "response.inject.failed", "error": {"code": "response_already_completed"}, "input": event["input"]}]
            if "previous_response_id" in event:
                return [lost]
            return [{"type": "response.created", "response": {"id": "first"}},
                    {"type": "response.output_item.done", "item": call}, complete([call])]
        for effort in ("auto", "ultra"):
            with self.subTest(effort=effort), tempfile.TemporaryDirectory() as folder:
                project = ProjectFiles(folder, "edit", threading.Event(), threading.Lock())
                stream = io.BytesIO(("data: " + json.dumps(complete([answer("Finished.")])) + "\n\n").encode())
                with patch("urllib.request.urlopen", return_value=stream) as http:
                    result, _, charges = self.run_live(upstream, project=project, review=lambda *_: True, effort=effort)
                self.assertEqual((Path(folder) / "new.txt").read_text(), "Created")
                self.assertEqual(len(result["workspace_activity"]), 1)
                self.assertEqual(result["text"], "Finished.")
                self.assertEqual([charge["status"] for charge in charges], ["done", "rejected", "done"])
                self.assertIsNone(charges[1]["usage"])
                body = json.loads(http.call_args.args[0].data)
                self.assertNotIn("previous_response_id", body)
                self.assertEqual([item.get("type") or item.get("role") for item in body["input"]],
                                 ["developer", "user", "function_call", "function_call_output"])


class ContextCheckpoints(unittest.TestCase):
    def test_reopen_compacted_chat_branch_and_model_switch(self):
        compact = {"type": "compaction", "id": "cp_1", "encrypted_content": "opaque"}
        config = {"type": "configuration_update", "reasoning": {"effort": "high"}}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = Store(root)
            start = [message("user", "Old decisions"), message("assistant", "Acknowledged"), message("user", "Next step")]
            cid = store.create("A long chat", messages=start)
            body = provider.request_body(start, SETTINGS, input_limit=4000)
            self.assertEqual(body["context_management"][0]["compact_threshold"], 3000)
            def fixture(key, request, stop):
                yield from provider.Decoder(request).feed(complete([compact, config, answer("Ready.")]))
            turn = harness.Turn(threading.Event())
            turn.send(fixture, "fixture", body, lambda *args, **kwargs: None, None, lambda result: None)
            final = turn.result()
            self.assertTrue(final["context_usage"]["compacted"])
            store.add(cid, {"role": "assistant", **final})
            history = Store(root).read(cid)["messages"] + [message("user", "Continue")]
            resumed = provider.request_body(history, SETTINGS)
            self.assertEqual(resumed["input"][1:4], [compact, config, answer("Ready.")])
            self.assertNotIn("Old decisions", str(resumed["input"]))
            self.assertIn("Old decisions", str(provider.request_body(history, SETTINGS | {"model": "gpt-5.4-mini"})["input"]))
            fork = store.fork(cid, 1)
            self.assertNotIn("opaque", str(provider.request_body(store.read(fork)["messages"], SETTINGS)))

    def test_oversized_existing_history_compacts_in_budget_before_generating(self):
        history = [message("user", letter * 4300) for letter in "abc"]
        history.append(message("user", "current question"))
        body = provider.request_body(history, SETTINGS, input_limit=2600)
        captured = []
        def fixture(key, request, stop):
            captured.append((request["input"][-1].get("type"), str(request["input"])))
            if request["input"][-1].get("type") == "compaction_trigger":
                items = [{"type": "compaction", "encrypted_content": "older-context"}]
            else:
                items = [answer("Done")]
            yield from provider.Decoder(request).feed(complete(items))
        turn = harness.Turn(threading.Event())
        charges = []
        turn.send(fixture, "fixture", body, lambda *args, **kwargs: None, None, charges.append)
        final = turn.result()
        self.assertEqual(final["text"], "Done")
        self.assertIn("older-context", str(final["compaction"]["items"]))
        self.assertEqual(captured[0][0], "compaction_trigger")
        self.assertIn("a" * 200, captured[0][1])
        self.assertIn("current question", captured[-1][1])
        self.assertIn("older-context", captured[-1][1])
        self.assertEqual(len(captured), len(charges))
