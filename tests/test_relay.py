"""Exercise the local HTTP app against a loopback Responses event stream."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from relay import codex, provider
from relay.config import ROOT
from relay.server import RelayServer
from relay.storage import Store


@contextmanager
def serving(server):
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        t.join()


class FakeOpenAI(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        if body["input"][-1]["content"][0].get("text") == "Simulate exhausted credits":
            payload = json.dumps({"error": {"code": "credit_balance_exhausted"}}).encode()
            self.send_response(429)
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for event in (
            {"type": "response.created"},
            {"type": "response.output_text.delta", "delta": "Hello, "},
            {"type": "response.output_text.delta", "delta": "let us continue."},
            {"type": "response.completed", "response": {"model": "gpt-5.4-mini-2026-03-17", "service_tier": "default",
             "usage": {"input_tokens": 14, "input_tokens_details": {"cached_tokens": 4}, "output_tokens": 6}}},
        ):
            self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
            self.wfile.flush()

    def log_message(self, *args):
        pass


class ChatFlow(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        api = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAI)
        api.requests = []
        self.sent = api.requests
        target = self.use(serving(api))
        self.use(patch.object(provider, "ENDPOINT", target + "/v1/responses"))
        self.server = RelayServer(0, self.root / "data", key_source=lambda: ("fixture-only", "test"), codex_home=self.root / "codex")
        self.url = self.use(serving(self.server))

    def use(self, cm):
        value = cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        return value

    def open(self, path, body=None, extra=None):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", **(extra or {})})
        return urllib.request.urlopen(req, timeout=5)

    def call(self, path, body=None):
        with self.open(path, body) as r:
            return json.load(r)

    def chat(self, **body):
        with self.open("/api/respond", body) as r:
            return [e for e in map(json.loads, r) if e["type"] != "ping"]

    def test_saved_chat_retry_and_real_sse_translation(self):
        stream = self.chat(message={"text": "First turn"})
        cid = stream[0]["id"]
        self.assertEqual("Hello, let us continue.", "".join(x["text"] for x in stream if x["type"] == "delta"))
        self.assertEqual(6, stream[-1]["usage"]["output_tokens"])
        self.chat(id=cid, message={"text": "Continue", "files": [{"kind": "text", "name": "notes.md", "data": "Note contents"}]})
        body = self.sent[-1]
        self.assertEqual([v["role"] for v in body["input"]], ["user", "assistant", "user"])
        self.assertIn("Note contents", body["input"][-1]["content"][1]["text"])
        self.assertIs(body["store"], False)
        self.chat(id=cid, retry=True)
        chat = Store(self.root / "data").read(cid)
        self.assertEqual(4, len(chat["messages"]))
        self.call("/api/chat/edit", {"id": cid, "title": "My notes", "pinned": True})
        self.assertEqual(self.call("/api/chats?q=" + urllib.parse.quote("First turn"))[0]["title"], "My notes")
        self.call("/api/chat/delete", {"id": cid})
        self.assertEqual(self.call("/api/chats"), [])

    def test_temporary_context_images_and_quota(self):
        img = {"kind": "image", "name": "pic.png", "data": "data:image/png;base64,abcd"}
        past = [{"role": "user", "text": "Previous question"}, {"role": "assistant", "text": "Understood"}]
        out = self.chat(temporary=True, history=past, message={"text": "Describe the image", "files": [img]})
        self.assertIsNone(out[0]["id"])
        self.assertEqual(self.sent[-1]["input"][-1]["content"][-1]["type"], "input_image")
        self.assertEqual(self.sent[-1]["input"][1]["content"], "Understood")
        out = self.chat(temporary=True, message={"text": "Simulate exhausted credits"})
        self.assertEqual(out[-1]["status"], "error")
        self.assertIn("credits exhausted", out[-1]["note"])
        self.assertEqual(self.call("/api/chats"), [])

    def test_selective_import_and_context_limit(self):
        name = "bbc56eda-3f07-4a67-8935-3f2e62f680ab"
        folder = self.server.codex_home / "archived_sessions"
        folder.mkdir(parents=True)
        (folder / f"rollout-2026-10-31T14-10-00-{name}.jsonl").write_bytes((ROOT / "examples/sample-rollout.jsonl").read_bytes())
        entries = self.call("/api/codex")
        self.assertTrue(entries[0]["archived"])
        msgs = self.call("/api/codex/" + name)["messages"]
        cid = self.call("/api/import", {"title": "Reading journal", "messages": msgs[:1]})["id"]
        self.assertEqual(len(self.call("/api/chats/" + cid)["messages"]), 1)
        self.assertEqual(len(self.sent), 0)
        settings = self.call("/api/status")["settings"]
        self.call("/api/settings", {**settings, "context": 8, "instructions": "Use plain English"})
        self.chat(temporary=True, history=[{"role": "user", "text": str(n)} for n in range(12)], message={"text": "Now"})
        self.assertEqual(len(self.sent[-1]["input"]), 8)
        self.assertEqual(self.sent[-1]["instructions"], "Use plain English")

    def test_credit_estimate_counts_temporary_chats_and_retries_without_content(self):
        self.assertIsNone(self.call("/api/balance")["meter"])
        self.call("/api/balance", {"amount": 10})
        self.chat(temporary=True, message={"text": "Temporary spending"})
        saved = self.chat(message={"text": "Saved spending"})[0]["id"]
        self.chat(id=saved, retry=True)
        self.chat(temporary=True, message={"text": "Simulate exhausted credits"})
        self.call("/api/chat/delete", {"id": saved})
        meter = self.call("/api/balance")["meter"]
        self.assertEqual((meter["priced"], meter["gaps"]), (3, 0))
        self.assertAlmostEqual(meter["remaining"], 10 - 3 * 0.0000348, places=9)
        self.assertEqual(self.call("/api/chats"), [])
        self.assertAlmostEqual(Store(self.root / "data").balance()["meter"]["spent"], 3 * 0.0000348)

    def test_reconciliation_skips_old_requests_and_exposes_pricing_gaps(self):
        store = self.server.store
        store.set_balance(12)
        old = store.meter_ticket("gpt-5.4-mini")
        store.set_balance(0)
        usage = {"usage": {"input_tokens": 100, "output_tokens": 50}}
        store.record_usage(old, usage)
        ticket = store.meter_ticket("custom-model")
        store.record_usage(ticket, usage)
        store.record_usage(ticket, {"usage": None})
        self.assertEqual(store.balance()["meter"]["gaps"], 2)
        self.call("/api/balance/rates", {"model": "custom-model", "rates": [2, 1, 4]})
        ticket = store.meter_ticket("custom-model")
        store.record_usage(ticket, usage)
        store.record_usage(ticket, {**usage, "service_tier": "priority"})
        meter = store.balance()["meter"]
        self.assertEqual((meter["priced"], meter["gaps"]), (1, 3))
        self.assertAlmostEqual(meter["remaining"], -0.0004)
        self.assertIsNone(self.call("/api/balance", {"amount": None})["meter"])

    def test_stop_preserves_partial_without_waiting_for_next_token(self):
        def slow(key, body, stopped):
            yield "delta", "Partial reply"
            stopped.wait(5)
        self.server.upstream = slow
        self.call("/api/balance", {"amount": 3})
        with self.open("/api/respond", {"message": {"text": "Slow response"}}) as r:
            start = json.loads(r.readline())
            self.assertEqual(json.loads(r.readline())["text"], "Partial reply")
            self.call("/api/stop", {"run": start["run"]})
            last = list(map(json.loads, r))[-1]
        self.assertEqual(last["status"], "stopped")
        self.assertEqual(self.call("/api/balance")["meter"]["gaps"], 1)
        self.assertEqual(self.call("/api/chats/" + start["id"])["messages"][-1]["text"], "Partial reply")

    def test_unconfigured_and_foreign_origin_never_call_provider(self):
        self.server.key_source = lambda: ("", "")
        self.assertFalse(self.call("/api/status")["configured"])
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.open("/api/respond", {"message": {"text": "hello"}})
        self.assertEqual(json.load(cm.exception)["code"], "no_key")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.open("/api/chats", extra={"Origin": "https://elsewhere.example"})
        self.assertEqual(cm.exception.code, 403)
        self.assertEqual(self.call("/api/chats"), [])
        self.assertEqual(self.sent, [])


class CodexFormats(unittest.TestCase):
    def test_current_and_legacy_transcripts_do_not_duplicate_or_include_tools(self):
        def event(t, **kw):
            return json.dumps({"type": "event_msg", "payload": {"type": t, **kw}})
        def response(role, text, **kw):
            return json.dumps({"type": "response_item", "payload": {"type": "message", "role": role,
                              "content": [{"type": "input_text", "text": text}], **kw}})
        lines = [response("developer", "internal"), event("task_started"),
                 response("user", "<environment_context>metadata"), response("user", "One"),
                 event("item_completed", item={"type": "UserMessage", "content": [{"type": "text", "text": "One"}]}),
                 event("item_completed", item={"type": "CommandExecution", "aggregated_output": "private"}),
                 event("agent_message", phase="commentary", message="progress"),
                 event("item_completed", item={"type": "AgentMessage", "phase": "final_answer", "content": [{"type": "Text", "text": "Two"}]}),
                 response("assistant", "Two", phase="final_answer"), event("task_complete", last_agent_message="Two"),
                 response("user", "Three"), response("assistant", "Four"), "unfinished json"]
        self.assertEqual([x["text"] for x in codex.visible(lines)], ["One", "Two", "Three", "Four"])
