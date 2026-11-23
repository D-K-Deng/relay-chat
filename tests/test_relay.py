"""Exercise the local HTTP app against a loopback Responses event stream."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from relay import codex, live, pricing, provider
from relay.config import ROOT
from relay.prompts import CHAT_INSTRUCTIONS
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
    def do_GET(self):
        if self.path != "/v1/models" or self.headers.get("Authorization") != "Bearer fixture-only":
            self.send_error(403)
            return
        entries = [
            "gpt-4o", "gpt-5.4-mini-2026-03-17", "gpt-6.2-sol", "gpt-5.5-pro",
            "gpt-realtime", "text-embedding-3-small",
        ]
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"data": [{"id": model, "created": i} for i, model in enumerate(entries)]}).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        question = body["input"][-1]["content"][0].get("text")
        if question == "Simulate exhausted credits":
            payload = json.dumps({"error": {"code": "credit_balance_exhausted"}}).encode()
            self.send_response(429)
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        events = (
            {"type": "response.created"},
            {"type": "response.output_text.delta", "delta": "Hello, "},
            {"type": "response.output_text.delta", "delta": "let us continue."},
            {"type": "response.completed", "response": {"model": "gpt-5.4-mini-2026-03-17", "service_tier": "default",
             "usage": {"input_tokens": 14, "input_tokens_details": {"cached_tokens": 4}, "output_tokens": 6}}},
        )
        if question == "Find an example" and {"type": "web_search"} in body.get("tools", []):
            events = (
                {"type": "response.web_search_call.searching"},
                {"type": "response.web_search_call.completed"},
                {"type": "response.output_text.delta", "delta": "Here is"},
                {"type": "response.incomplete", "response": {
                    "model": "gpt-6.2-sol", "service_tier": "fast",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                    "tool_usage": {"web_search": {"num_requests": 1}},
                    "output": [
                        {"type": "web_search_call", "action": {"type": "search"}, "status": "completed"},
                        {"type": "web_search_call", "action": {"type": "search"}, "status": "completed"},
                        {"type": "message", "content": [{"type": "output_text", "text": "Here is a source.",
                            "annotations": [{"type": "url_citation", "start_index": 10, "end_index": 17,
                                             "title": "Example source", "url": "https://example.com/"}]}]},
                    ],
                }},
            )
        for event in events:
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
        self.use(patch.object(provider, "MODELS_ENDPOINT", target + "/v1/models"))
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

    def test_drafts_keep_latest_text_and_follow_chat_deletion(self):
        cid = self.server.store.create("Drafted chat")
        def save(key, text, revision):
            self.call("/api/draft", {"id": key, "text": text, "revision": revision})

        save("new", "Unsent\n新问题", 1)
        save(cid, "Later edit", 2)
        save(cid, "Earlier edit", 1)
        save("temporary", "Window only", 3)
        self.assertEqual(Store(self.root / "data").drafts(), {"new": "Unsent\n新问题", cid: "Later edit"})
        save("new", "", 3)
        save("new", "Late arrival", 2)
        self.call("/api/chat/delete", {"id": cid})
        save(cid, "Deleted", 4)
        self.assertEqual(self.call("/api/drafts"), {})

    def test_saved_chat_retry_and_real_sse_translation(self):
        question = "请解释这段英文。"
        stream = self.chat(message={"text": question})
        cid = stream[0]["id"]
        first_request = self.sent[-1]["input"]
        self.assertEqual(first_request[0]["role"], "developer")
        self.assertTrue(first_request[0]["content"].startswith(CHAT_INSTRUCTIONS))
        self.assertEqual(first_request[1]["content"][0]["text"], question)
        self.assertEqual(stream[0]["count"], 1)
        self.assertEqual("Hello, let us continue.", "".join(x["text"] for x in stream if x["type"] == "delta"))
        self.assertEqual(6, stream[-1]["usage"]["output_tokens"])
        self.chat(id=cid, message={"text": "Continue", "files": [{"kind": "text", "name": "notes.md", "data": "Note contents"}]})
        body = self.sent[-1]
        self.assertEqual([v["role"] for v in body["input"]], ["developer", "user", "assistant", "user"])
        self.assertIn("Note contents", body["input"][-1]["content"][1]["text"])
        self.assertIs(body["store"], False)
        self.chat(id=cid, retry=True)
        self.assertEqual(self.sent[-1]["input"][0], first_request[0])
        chat = Store(self.root / "data").read(cid)
        self.assertEqual(4, len(chat["messages"]))
        self.call("/api/chat/edit", {"id": cid, "title": "My notes", "pinned": True})
        self.assertEqual(self.call("/api/chats?q=" + urllib.parse.quote(question))[0]["title"], "My notes")
        self.call("/api/chat/delete", {"id": cid})
        self.assertEqual(self.call("/api/chats"), [])

    def test_fork_copies_through_selected_reply_and_keeps_source_intact(self):
        store = self.server.store
        review = {"tool": "edit_file", "path": "intro.md", "status": "done", "detail": "Applied edit",
                  "diff": "-Hi\n+Hello"}
        first = {"role": "assistant", "text": "Updated the greeting.", "workspace_activity": [review],
                 "reasoning_summary": "I checked the introduction.", "subagents": ["/root/editor"]}
        cid = store.create("Greeting", messages=[{"role": "user", "text": "Update the greeting."}, first,
                                                 {"role": "user", "text": "Next question."}])
        branch = self.call("/api/chat/fork", {"id": cid, "through": 1})["id"]
        copied = self.call("/api/chats/" + branch)
        self.assertEqual(copied["messages"], store.read(cid)["messages"][:2])
        self.assertEqual(copied["source"], "branch")
        self.assertEqual(copied["workspace_mode"], "off")
        self.chat(id=branch, message={"text": "An alternative"})
        self.assertEqual(len(store.read(branch)["messages"]), 4)
        self.assertEqual(len(store.read(cid)["messages"]), 3)

    def test_temporary_context_images_and_quota(self):
        img = {"kind": "image", "name": "pic.png", "data": "data:image/png;base64,abcd"}
        past = [{"role": "user", "text": "Previous question"}, {"role": "assistant", "text": "Understood"}]
        out = self.chat(temporary=True, history=past, message={"text": "Describe the image", "files": [img]})
        self.assertIsNone(out[0]["id"])
        self.assertEqual(self.sent[-1]["input"][-1]["content"][-1]["type"], "input_image")
        self.assertEqual(self.sent[-1]["input"][2]["content"], "Understood")
        out = self.chat(temporary=True, message={"text": "Simulate exhausted credits"})
        self.assertEqual(out[-1]["status"], "error")
        self.assertIn("credits exhausted", out[-1]["note"])
        self.assertEqual(self.call("/api/chats"), [])

    def test_model_catalog_and_composer_settings_reach_responses(self):
        catalog = {m["id"]: m for m in self.call("/api/models")["models"]}
        self.assertEqual(set(catalog), {"gpt-4o", "gpt-5.4-mini-2026-03-17", "gpt-6.2-sol"})
        self.assertIn("none", catalog["gpt-5.4-mini-2026-03-17"]["efforts"])
        self.assertEqual(catalog["gpt-6.2-sol"]["tiers"], ["default", "fast", "ultrafast"])
        self.assertNotIn("none", catalog["gpt-6.2-sol"]["efforts"])
        self.call("/api/settings", {"context": 8, "instructions": "Keep it short"})
        self.call("/api/settings", {"model": "gpt-6.2-sol"})
        self.call("/api/settings", {"effort": "max"})
        self.call("/api/settings", {"service_tier": "ultrafast"})
        self.chat(temporary=True, message={"text": "Hello"})
        request = self.sent[-1]
        self.assertEqual(request["model"], "gpt-6.2-sol")
        self.assertEqual(request["reasoning"], {"effort": "max"})
        self.assertEqual(request["service_tier"], "ultrafast")
        self.assertEqual(request["input"][0], {
            "role": "developer", "content": CHAT_INSTRUCTIONS + "\n\nUser's standing preferences\nKeep it short",
        })
        self.assertEqual(request["input"][-1]["content"][0]["text"], "Hello")
        self.assertNotIn("max_output_tokens", request)
        self.assertEqual(request["tools"], [{"type": "web_search"}])
        settings = self.call("/api/settings", {"model": "gpt-4o"})
        self.assertEqual((settings["effort"], settings["service_tier"], settings["context"]), ("auto", "default", 8))
        self.chat(temporary=True, message={"text": "Hello"})
        self.assertNotIn("reasoning", self.sent[-1])
        self.assertEqual(self.sent[-1]["service_tier"], "default")

    def test_search_partial_response_persists_authoritative_text_sources_and_cost(self):
        self.call("/api/settings", {"model": "gpt-6.2-sol", "service_tier": "fast", "max_output": 512})
        self.call("/api/balance", {"amount": 10})
        stream = self.chat(web_search=True, message={"text": "Find an example"})
        self.assertIn({"type": "web_search"}, self.sent[-1]["tools"])
        self.assertEqual(self.sent[-1]["tool_choice"], "auto")
        self.assertEqual(self.sent[-1]["max_output_tokens"], 512)
        self.assertEqual([e["status"] for e in stream if e["type"] == "search"], ["searching", "completed"])
        final = stream[-1]
        self.assertEqual((final["status"], final["reason"]), ("incomplete", "max_output_tokens"))
        saved = self.call("/api/chats/" + stream[0]["id"])["messages"][-1]
        self.assertEqual(saved["text"], "Here is a source.")
        self.assertEqual(saved["sources"][0]["url"], "https://example.com/")
        self.assertEqual(saved["sources"], final["sources"])
        self.assertEqual(saved["reason"], final["reason"])
        balance = self.call("/api/balance")
        self.assertEqual(balance["tier_rates"], [4, 0.2, 20])
        self.assertAlmostEqual(balance["meter"]["spent"], 0.0114)

    def test_saved_search_default_and_per_request_override(self):
        store = self.server.store
        legacy = {"model": "gpt-6.2-sol", "effort": "ultra", "service_tier": "ultrafast"}
        with store.db() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES(1,?)", (json.dumps(legacy),))
        status = self.call("/api/status")
        self.assertTrue(status["settings"]["web_search"])
        self.assertEqual(status["model_capabilities"]["id"], legacy["model"])
        self.call("/api/settings", {"web_search": False})
        saved = Store(self.root / "data").settings()
        self.assertEqual({k: saved[k] for k in legacy}, legacy)
        self.assertFalse(saved["web_search"])
        self.chat(temporary=True, message={"text": "Search is disabled"})
        self.assertNotIn("tools", self.sent[-1])
        self.call("/api/settings", {"web_search": True})
        self.chat(temporary=True, message={"text": "Use my preferences"})
        self.assertEqual(self.sent[-1]["tools"], [{"type": "web_search"}])
        self.chat(temporary=True, web_search=False, message={"text": "Skip search this time"})
        self.assertNotIn("tools", self.sent[-1])
        self.call("/api/settings", {"model": "o3-mini"})
        self.chat(temporary=True, message={"text": "Use my preferences"})
        self.assertNotIn("tools", self.sent[-1])
        self.assertTrue(self.call("/api/status")["settings"]["web_search"])

    def test_output_and_context_defaults_migrate_legacy_settings(self):
        store = self.server.store
        store.configure({"context": 32, "max_output": 4096})
        with store.db() as db:
            db.execute("ALTER TABLE messages DROP COLUMN response_details")
        migrated = Store(self.root / "data")
        settings = migrated.settings()
        self.assertEqual((settings["context"], settings["max_output"]), (0, 0))
        selected = provider.request_body(
            [{"role": "user", "text": "Old context " * 30}, {"role": "user", "text": "Latest turn"}],
            settings, input_limit=provider.message_tokens({"text": CHAT_INSTRUCTIONS}) + 40,
        )
        self.assertEqual(len(selected["input"]), 2)
        self.assertEqual(selected["input"][0]["content"], CHAT_INSTRUCTIONS)
        self.assertEqual(selected["input"][1]["content"][0]["text"], "Latest turn")
        self.assertEqual(selected["truncation"], "auto")
        self.assertNotIn("max_output_tokens", selected)

    def test_legacy_plain_chat_gets_tools_once_and_keeps_later_opt_out(self):
        store = self.server.store
        old_chat = store.create("Older chat", workspace_mode="off")
        imported = store.create("Imported chat", source="codex")
        project = store.create("Chosen project", workspace_mode="read", workspace_directory=str(self.root),
                               workspace_auto_review=False, workspace_terminal=False)
        store.configure({"web_search": False})
        with store.db() as db:
            db.execute("PRAGMA user_version=0")
        reopened = Store(self.root / "data")
        binding = reopened.chat_workspace(old_chat)
        self.assertEqual(binding["workspace_mode"], "chat")
        self.assertTrue(binding["workspace_auto_review"])
        self.assertTrue(binding["workspace_terminal"])
        self.assertFalse(reopened.settings()["web_search"])
        self.assertEqual(reopened.chat_workspace(imported)["workspace_mode"], "off")
        self.assertFalse(reopened.chat_workspace(project)["workspace_terminal"])
        self.assertEqual(reopened.chat_workspace(project)["workspace_directory"], str(self.root))
        reopened.set_chat_workspace(old_chat, "off", "")
        self.assertEqual(Store(self.root / "data").chat_workspace(old_chat)["workspace_mode"], "off")

    def test_astra_ultrafast_published_pricing(self):
        before = datetime(2026, 11, 22, tzinfo=timezone.utc)
        after = datetime(2027, 3, 2, tzinfo=timezone.utc)
        for when, expected in ((before, (24, 1.2, 120)), (after, (48, 2.4, 240))):
            with self.subTest(at=when):
                for rate, value in zip(pricing.model_rates("gpt-6.2-astra", "ultrafast", at=when), expected):
                    self.assertAlmostEqual(rate, value)

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
        self.call("/api/settings", {**settings, "context": 8, "instructions": "Use metric units"})
        self.chat(temporary=True, history=[{"role": "user", "text": str(n)} for n in range(12)], message={"text": "Now"})
        self.assertEqual(len(self.sent[-1]["input"]), 9)
        self.assertTrue(self.sent[-1]["input"][0]["content"].endswith("Use metric units"))

    def test_credit_estimate_counts_temporary_chats_and_retries_without_content(self):
        self.assertIsNone(self.call("/api/balance")["meter"])
        self.call("/api/balance", {"amount": 10})
        self.chat(temporary=True, message={"text": "Temporary spending"})
        saved = self.chat(message={"text": "Saved spending"})[0]["id"]
        self.chat(id=saved, retry=True)
        self.chat(temporary=True, message={"text": "Simulate exhausted credits"})
        self.call("/api/chat/delete", {"id": saved})
        meter = self.call("/api/balance")["meter"]
        self.assertEqual((meter["priced"], meter["gaps"], meter["failed"], meter["estimated"]), (3, 0, 1, 0))
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

    def test_saved_auto_wait_default_and_per_turn_override(self):
        self.assertTrue(self.call("/api/status")["settings"]["auto_wait_rate_limits"])
        self.call("/api/settings", {"auto_wait_rate_limits": False})
        self.assertFalse(Store(self.root / "data").settings()["auto_wait_rate_limits"])
        self.call("/api/balance", {"amount": 1})
        attempts = []
        def upstream(key, request, stopped):
            attempts.append(request)
            if len(attempts) <= 4:
                yield "end", {"status": "error", "note": "Rate limit reached", "rejected": True,
                              "rate_limit": {"retry_after": .169, "metric": "TPM"}}
            else:
                yield "end", {"status": "done", "text": "Ready", "usage": {"input_tokens": 5, "output_tokens": 1}}
        self.server.upstream = upstream
        payload = {"temporary": True, "message": {"text": "Hello"}}
        off = self.chat(**payload)
        self.assertEqual((off[-1]["status"], len(attempts)), ("error", 1))
        self.assertNotIn("rate_wait", [event["type"] for event in off])
        attempts.clear()
        with patch.object(live.RunControl, "wait", return_value=False):
            on = self.chat(**payload, model_settings={"auto_wait_rate_limits": True})
        self.assertEqual((on[-1]["text"], len(attempts)), ("Ready", 5))
        self.assertNotIn("auto_wait_rate_limits", attempts[0])
        self.assertEqual(sum(event["type"] == "rate_wait" for event in on), 4)
        meter = self.call("/api/balance")["meter"]
        self.assertEqual((meter["failed"], meter["gaps"], meter["priced"]), (5, 0, 1))

    def test_stop_preserves_partial_without_waiting_for_next_token(self):
        def slow(key, body, stopped):
            yield "delta", "Partial reply"
            stopped.wait(5)
        self.server.upstream = slow
        self.call("/api/balance", {"amount": 3})
        with self.open("/api/respond", {"message": {"text": "Slow response"}}) as r:
            start = json.loads(r.readline())
            event = json.loads(r.readline())
            if event["type"] == "context":
                event = json.loads(r.readline())
            self.assertEqual((event["type"], event["text"]), ("delta", "Partial reply"))
            self.call("/api/stop", {"run": start["run"]})
            last = list(map(json.loads, r))[-1]
        self.assertEqual(last["status"], "stopped")
        meter = self.call("/api/balance")["meter"]
        self.assertEqual((meter["priced"], meter["gaps"], meter["estimated"]), (1, 0, 1))
        self.assertGreater(meter["estimated_spend"], 0)
        self.assertTrue(last["usage"]["estimated"])
        self.assertEqual(self.call("/api/chats/" + start["id"])["messages"][-1]["text"], "Partial reply")

    def test_failed_ultra_reply_counts_worker_output_and_requested_speed_once(self):
        def interrupted(key, body, stopped):
            yield "agent_activity", {"name": "/root/test", "id": "worker", "delta": "12345678"}
            yield "agent_activity", {"name": "/root/test", "id": "worker", "text": "12345678", "done": True}
            yield "delta", "1234"
            yield "error", "WebSocket connection closed during generation"
        self.server.upstream = interrupted
        self.call("/api/balance", {"amount": 5})
        payload = {"model": "gpt-6.2-astra", "service_tier": "ultrafast", "effort": "ultra"}
        events = self.chat(message={"text": "Parallel task"}, model_settings=payload)
        cid = events[0]["id"]
        reply = self.call("/api/chats/" + cid)["messages"][-1]
        usage = reply["usage"]
        self.assertEqual((reply["status"], reply["text"], usage["estimated"], usage["output_tokens"]),
                         ("error", "1234", True, 3))
        self.assertGreater(usage["input_tokens"], 0)
        expected = pricing.estimate_usd("gpt-6.2-astra", usage, "ultrafast")
        self.call("/api/chat/delete", {"id": cid})
        meter = Store(self.root / "data").balance()["meter"]
        self.assertEqual((meter["failed"], meter["estimated"], meter["priced"], meter["gaps"]), (1, 1, 1, 0))
        self.assertAlmostEqual(meter["spent"], expected)
        self.assertAlmostEqual(meter["estimated_spend"], expected)

    def test_failed_reply_keeps_reported_usage_and_settled_segments(self):
        part = {"input_tokens": 20, "output_tokens": 9, "input_tokens_details": {"cached_tokens": 5}}
        def two_segments(key, body, stopped):
            yield "delta", "First segment"
            yield "segment", {"status": "done", "usage": part, "service_tier": "fast"}
            yield "delta", "Partial final output"
            yield "end", {"status": "error", "usage": part, "service_tier": "default", "note": "Server error"}
        self.server.upstream = two_segments
        self.call("/api/balance", {"amount": 1})
        model = "gpt-6.2-sol"
        result = self.chat(temporary=True, message={"text": "Task"}, model_settings={"model": model})[-1]
        self.assertEqual(result["usage"], {"input_tokens": 40, "output_tokens": 18,
                                           "input_tokens_details": {"cached_tokens": 10}})
        meter = self.call("/api/balance")["meter"]
        self.assertEqual((meter["failed"], meter["estimated"], meter["priced"], meter["gaps"]), (1, 0, 2, 0))
        self.assertAlmostEqual(meter["spent"], sum(pricing.estimate_usd(model, part, t) for t in ("fast", "default")))

    def test_concurrent_chats_keep_their_model_approval_stop_and_files(self):
        requests = []
        def create_note(key, body, stopped):
            question = next(item["content"][0]["text"] for item in body["input"] if item.get("role") == "user")
            requests.append((question, body["model"], body["service_tier"], body.get("reasoning"),
                             body.get("max_output_tokens")))
            if body["input"][-1].get("type") == "function_call_output":
                yield "end", {"status": "done", "text": "Saved " + question}
            else:
                call = {"type": "function_call", "call_id": "write", "name": "create_file",
                        "arguments": json.dumps({"path": "note.txt", "content": question})}
                yield "end", {"status": "done", "output_items": [call]}

        self.server.upstream = create_note
        self.call("/api/settings", {"model": "gpt-4o"})
        first = {"message": {"text": "First"}, "workspace_auto_review": False, "model_settings": {
            "model": "gpt-6.2-sol", "effort": "high", "service_tier": "fast", "max_output": 512}}
        second = {"message": {"text": "Second"}, "workspace_auto_review": False}
        with self.open("/api/respond", first) as one, self.open("/api/respond", second) as two:
            a, b = json.loads(one.readline()), json.loads(two.readline())
            approval_a = next(e for e in map(json.loads, one) if e["type"] == "approval")
            approval_b = next(e for e in map(json.loads, two) if e["type"] == "approval")
            self.assertNotEqual(a["run"], b["run"])
            self.assertNotEqual(a["id"], b["id"])
            with self.assertRaises(urllib.error.HTTPError) as duplicate:
                self.call("/api/respond", second | {"id": a["id"]})
            self.assertIn("already in progress", json.load(duplicate.exception)["error"])
            with self.assertRaises(urllib.error.HTTPError) as wrong_approval:
                self.call("/api/workspace/decision", {"run": a["run"], "id": approval_b["id"], "allow": True})
            self.assertEqual(wrong_approval.exception.code, 400)
            wrong_approval.exception.close()
            self.call("/api/settings", {"service_tier": "fast"})
            self.call("/api/stop", {"run": b["run"]})
            self.assertEqual(list(map(json.loads, two))[-1]["status"], "stopped")
            self.call("/api/workspace/decision", {"run": a["run"], "id": approval_a["id"], "allow": True})
            self.assertEqual(list(map(json.loads, one))[-1]["status"], "done")
        store = self.server.store
        self.assertEqual(requests.count(("First", "gpt-6.2-sol", "fast", {"effort": "high"}, 512)), 2)
        self.assertIn(("Second", "gpt-4o", "default", None, None), requests)
        self.assertEqual(store.settings()["model"], "gpt-4o")
        self.assertEqual(store.read(a["id"])["messages"][-1]["text"], "Saved First")
        self.assertEqual(store.read(b["id"])["messages"][-1]["status"], "stopped")
        self.assertEqual((store.chat_folder(a["id"]) / "note.txt").read_text(), "First")
        self.assertFalse((store.chat_folder(b["id"]) / "note.txt").exists())

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
