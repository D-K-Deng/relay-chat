"""Local Responses fixture exercising file tools, approvals, and stateless replay."""
import json
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from relay import provider
from relay.project_files import ProjectFiles
from relay.server import RelayServer


class ResponsesFixture(BaseHTTPRequestHandler):
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(request)
        def is_review(body):
            return body.get("text", {}).get("format", {}).get("name") == "workspace_review"
        n = sum(not is_review(body) for body in self.server.requests)
        if is_review(request):
            verdict = getattr(self.server, "review_answer", {"decision": "approve", "reason": "A scoped wording change."})
            output = [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(verdict)}]}]
        elif n <= len(self.server.actions):
            name, args = self.server.actions[n - 1]
            output = [{"type": "reasoning", "id": f"rs_{n}", "encrypted_content": "encrypted fixture"},
                      {"type": "function_call", "agent": {"agent_name": "/root/worker"},
                       "name": name, "call_id": f"call_{n}", "arguments": json.dumps(args)}]
        else:
            output = [{"type": "message", "content": [{"type": "output_text", "text": "Finished."}]}]
        event = {"type": "response.completed", "response": {"model": request["model"], "output": output,
                 "usage": {"input_tokens": 10, "output_tokens": 3, "input_tokens_details": {"cached_tokens": 1}}}}
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())

    def log_message(self, *args):
        pass


class WorkspaceChat(unittest.TestCase):
    def serve(self, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.folder = (root / "project").resolve()
        self.folder.mkdir()
        self.file = self.folder / "hello.txt"
        self.file.write_bytes(b"hello\r\nworld\r\n")
        self.api = ThreadingHTTPServer(("127.0.0.1", 0), ResponsesFixture)
        self.api.requests = []
        self.api.actions = []
        endpoint = patch.object(provider, "ENDPOINT", self.serve(self.api) + "/v1/responses")
        endpoint.start()
        self.addCleanup(endpoint.stop)
        self.app = RelayServer(0, root / "data", key_source=lambda: ("fixture", "test"), codex_home=root / "codex")
        self.url = self.serve(self.app)
        self.app.store.configure({"workspace_directory": str(self.folder), "model": "gpt-6.2-sol", "effort": "ultra"})

    def post(self, path, body):
        return urllib.request.urlopen(urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                      headers={"Content-Type": "application/json"}), timeout=5)

    def run_turn(self, mode="edit", review=None, cid=None, **options):
        body = {"message": {"text": "Work on the project."}}
        if mode is not None:
            body.update(workspace_mode=mode, workspace_directory=str(self.folder))
        body.update(options)
        if cid:
            body["id"] = cid
        with self.post("/api/respond", body) as stream:
            start = json.loads(stream.readline())
            self.events = []
            for event in map(json.loads, stream):
                self.events.append(event)
                if event["type"] == "approval":
                    self.assertIsNotNone(review, "Unexpected write request")
                    review(start["run"], event)
                if event["type"] == "finish":
                    return start, event
        self.fail("Missing finish event")

    def decide(self, run, event, allow):
        with self.post("/api/workspace/decision", {"run": run, "id": event["id"], "allow": allow}) as response:
            self.assertTrue(json.load(response)["ok"])

    def test_regular_chats_use_their_own_files_and_default_tools(self):
        self.api.actions = [("run_terminal", {"cwd": ".", "argv": ["git", "--version"]}),
                            ("create_file", {"path": "greeting.txt", "content": "Hello."})]
        start, result = self.run_turn(mode=None, message={"text": "Check the Git version and create greeting.txt with Hello."})
        cid = start["id"]
        settings = self.app.store.settings()
        for key in ("web_search", "workspace_auto_review", "workspace_terminal"):
            self.assertIs(settings[key], True)
        tools = self.api.requests[0]["tools"]
        self.assertIn({"type": "web_search"}, tools)
        self.assertIn("run_terminal", {t.get("name") for t in tools})
        command, creation = result["workspace_activity"]
        agent_tools = result["agent_activity"]["/root/worker"]
        self.assertEqual(list(agent_tools), ["call_1", "call_2"])
        self.assertIn("Exited with code 0", agent_tools["call_1"]["text"])
        self.assertEqual(agent_tools["call_2"]["target"], "greeting.txt")
        self.assertEqual(agent_tools["call_2"]["stage"], "done")
        self.assertEqual((command["review"]["source"], command["exit_code"]), ("auto", 0))
        self.assertIn("git version", command["output"])
        self.assertEqual((creation["review"]["source"], creation["status"]), ("auto", "done"))
        folder = Path(command["directory"])
        self.assertTrue(folder.is_relative_to(self.app.store.path.parent.resolve()))
        self.assertEqual((folder / "greeting.txt").read_text(), "Hello.")
        self.assertFalse((self.folder / "greeting.txt").exists())

        self.api.actions = [("read_file", {"path": "greeting.txt", "start": 1, "count": 1})]
        self.api.requests.clear()
        self.run_turn(mode=None, cid=cid)
        self.assertIn("1: Hello.", self.api.requests[-1]["input"][-1]["output"])
        self.api.requests.clear()
        other, result = self.run_turn(mode=None)
        self.assertNotEqual(other["id"], cid)
        self.assertEqual(result["workspace_activity"][0]["status"], "error")

    def test_delete_external_outputs_after_workspace_switch_or_user_edit(self):
        self.api.actions = [("create_file", {"path": "generated/note.txt", "content": "One"}),
                            ("edit_file", {"path": "generated/note.txt", "old": "One", "new": "Two"}),
                            ("create_file", {"path": "keep.txt", "content": "Relay"}),
                            ("edit_file", {"path": "hello.txt", "old": "hello", "new": "Edited"})]
        start, _ = self.run_turn(review=lambda run, event: self.decide(run, event, True))
        cid = start["id"]
        self.app.store.set_chat_workspace(cid, "chat", "")
        with self.post("/api/chat/delete-preview", {"id": cid}) as response:
            plan = json.load(response)
        self.assertEqual(set(plan["project_files"]), {str(self.folder / "generated/note.txt"), str(self.folder / "keep.txt")})
        (self.folder / "keep.txt").write_text("Personal revision.")
        with self.post("/api/chat/delete", {"id": cid, "remove_folder": True,
                       "project_files": [*plan["project_files"], str(self.file)]}) as response:
            self.assertEqual(json.load(response)["changed"], [str(self.folder / "keep.txt")])
        self.assertFalse((self.folder / "generated").exists())
        self.assertFalse(self.app.store.chat_folder(cid).exists())
        self.assertEqual((self.folder / "keep.txt").read_text(), "Personal revision.")
        self.assertIn("Edited", self.file.read_text())

    def test_temporary_cleanup_tracks_only_its_own_created_files(self):
        self.api.actions = [("create_file", {"path": "tmp/a.txt", "content": "Temporary result"})]
        self.run_turn(temporary=True, temporary_session="one", review=lambda run, event: self.decide(run, event, True))
        with self.app.store.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM project_creations").fetchone()[0], 0)
        with self.post("/api/chat/delete-preview", {"temporary_session": "two"}) as response:
            self.assertEqual(json.load(response)["project_files"], [])
        params = {"temporary_session": "one"}
        with self.post("/api/chat/delete-preview", params) as response:
            plan = json.load(response)
        self.assertEqual(plan, {"folder": None, "project_files": [str(self.folder / "tmp/a.txt")]})
        with self.post("/api/chat/delete", params | {"project_files": plan["project_files"]}):
            pass
        self.assertFalse((self.folder / "tmp").exists())
        self.assertFalse(self.app.store.temporary_creations)
        self.assertFalse(self.app.store.temporary_projects)

    def test_recreated_external_file_belongs_to_new_chat_and_shared_root_survives(self):
        store = self.app.store
        create = ("create_file", {"path": "generated/new.txt", "content": "File"})
        self.api.actions = [create, ("create_file", {"path": "retained/note.txt", "content": "Note"})]
        apply = lambda run, event: self.decide(run, event, True)
        first, _ = self.run_turn(review=apply)
        file = self.folder / "generated/new.txt"
        file.unlink()
        self.api.actions = [create]
        self.api.requests.clear()
        second, _ = self.run_turn(review=apply)
        retained = self.folder / "retained/note.txt"
        store.set_chat_workspace(second["id"], "read", str(retained.parent))
        store.delete(first["id"], project_files=[str(file), str(retained)])
        self.assertEqual(file.read_text(), "File")
        self.assertFalse(retained.exists())
        self.assertTrue(retained.parent.is_dir())
        store.delete(second["id"], project_files=[str(file)])
        self.assertFalse(file.exists())

    def test_chat_directory_shared_with_custom_project_is_kept(self):
        store = self.app.store
        owner = store.create("Files")
        folder = store.chat_folder(owner)
        nested = folder / "selected"
        nested.mkdir(parents=True)
        other = store.create("Reading the project", workspace_mode="read", workspace_directory=str(nested))
        for project_root in (nested, folder, folder.parent):
            store.set_chat_workspace(other, "read", str(project_root))
            with self.post("/api/chat/delete-preview", {"id": owner}) as response:
                self.assertEqual(json.load(response)["shared_folder"], str(folder))
            with self.assertRaisesRegex(ValueError, "shared"):
                store.delete(owner, remove_folder=True)
        store.delete(owner)
        self.assertTrue(nested.is_dir())
        self.assertTrue(store.delete(owner, remove_folder=True)["ok"])
        self.assertTrue(nested.is_dir())

    def test_find_search_read_review_write_and_replay(self):
        (self.folder / "AGENTS.md").write_text("Preserve line endings.", encoding="utf-8")
        self.api.actions = [("find_files", {"path": ".", "pattern": "*.txt"}),
                            ("search_text", {"path": ".", "pattern": "*.txt", "query": "world"}),
                            ("read_file", {"path": "hello.txt", "start": 1, "count": 2}),
                            ("edit_file", {"path": "hello.txt", "old": "hello\nworld", "new": "hi\nworld"})]
        self.app.store.set_balance(2)

        def approve(run, event):
            self.assertEqual(self.file.read_bytes(), b"hello\r\nworld\r\n")
            self.assertIn("-hello", event["diff"])
            self.assertIn("+hi", event["diff"])
            self.decide(run, event, True)

        start, final = self.run_turn(review=approve)
        self.assertEqual(self.file.read_bytes(), b"hi\r\nworld\r\n")
        self.assertEqual([e["status"] for e in final["workspace_activity"]], ["done"] * 4)
        self.assertEqual([e["tool"] for e in final["workspace_activity"]], [a[0] for a in self.api.actions])
        saved = self.app.store.read(start["id"])["messages"][-1]
        self.assertEqual(saved["workspace_activity"], final["workspace_activity"])
        self.assertIn("+hi", saved["workspace_activity"][-1]["diff"])
        self.assertEqual(saved["text"], "Finished.")
        self.assertNotIn("encrypted fixture", json.dumps(final))
        self.assertEqual(final["usage"]["input_tokens"], 50)
        self.assertEqual(self.app.store.balance()["meter"]["priced"], 5)
        final_input = self.api.requests[-1]["input"]
        outputs = [i["output"] for i in final_input if i.get("type") == "function_call_output"]
        self.assertIn("hello.txt", outputs[0])
        self.assertIn("hello.txt:2: world", outputs[1])
        self.assertIn("2: world", outputs[2])
        self.assertEqual(outputs[3], "Applied edit")
        self.assertEqual(len([i for i in final_input if i.get("encrypted_content")]), 4)
        self.assertTrue(all(request["store"] is False for request in self.api.requests))
        self.assertIn("Preserve line endings.", self.api.requests[0]["input"][-1]["content"][0]["text"])

    def test_saved_chat_binding_survives_default_folder_change(self):
        start, _ = self.run_turn(mode="read")
        cid = start["id"]
        self.app.store.configure({"workspace_directory": ""})
        self.run_turn(mode="read", cid=cid)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.run_turn(mode="edit", cid=cid)
        self.assertEqual(cm.exception.code, 400)
        with self.post("/api/chat/workspace", {"id": cid, "mode": "edit", "directory": str(self.folder)}) as r:
            self.assertEqual(json.load(r)["workspace_mode"], "edit")
        self.run_turn(mode="edit", cid=cid)
        self.assertIn("create_file", {tool.get("name") for tool in self.api.requests[-1]["tools"]})

    def test_denial_read_only_and_stop_cannot_write(self):
        self.api.actions = [("create_file", {"path": "new.txt", "content": "Proposal"})]
        _, result = self.run_turn(review=lambda run, event: self.decide(run, event, False))
        self.assertEqual(result["workspace_activity"][0]["status"], "denied")
        self.api.requests.clear()
        _, result = self.run_turn(mode="read")
        self.assertEqual(result["workspace_activity"][0]["status"], "error")
        self.assertNotIn("create_file", {tool.get("name") for tool in self.api.requests[0]["tools"]})
        self.api.requests.clear()

        def stop(run, event):
            self.post("/api/stop", {"run": run}).close()

        _, result = self.run_turn(review=stop)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(len(self.api.requests), 1)
        self.assertFalse((self.folder / "new.txt").exists())
        self.assertFalse(self.app.approvals)

    def test_changed_file_and_workspace(self):
        self.api.actions = [("edit_file", {"path": "hello.txt", "old": "world", "new": "friend"})]
        def edit_elsewhere(run, event):
            self.file.write_text("An external edit", encoding="utf-8")
            self.decide(run, event, True)
        _, result = self.run_turn(review=edit_elsewhere)
        self.assertEqual(self.file.read_text(encoding="utf-8"), "An external edit")
        self.assertIn("changed during review", result["workspace_activity"][0]["detail"])
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/api/respond", {"workspace_mode": "read", "workspace_directory": "old selection",
                                      "message": {"text": "Find a file."}})
        self.assertEqual(cm.exception.code, 400)

    def test_folder_scope_and_excluded_files(self):
        (self.folder / ".env.local").write_text("secret", encoding="utf-8")
        tools = ProjectFiles(self.folder, "edit", threading.Event(), threading.Lock())
        for path in (".env.local", "../data/relay.sqlite3", str(self.file), ".git/config"):
            entry, _ = tools.execute("read_file", {"path": path, "start": 1, "count": 3}, None)
            self.assertEqual(entry["status"], "error", path)
        entry, result = tools.execute("search_text", {"path": ".", "pattern": "*", "query": "secret"}, None)
        self.assertEqual((entry["status"], result), ("done", "0 matches"))
        entry, result = tools.execute("create_file", {"path": "nested/新.txt", "content": "初稿"}, lambda p: True)
        self.assertEqual(entry["status"], "done")
        self.assertEqual((self.folder / "nested/新.txt").read_text(encoding="utf-8"), "初稿")

    def test_auto_review_is_independent_and_billable(self):
        self.app.store.set_balance(2)
        self.api.actions = [("edit_file", {"path": "hello.txt", "old": "hello", "new": "howdy"})]
        start, final = self.run_turn(workspace_auto_review=True, message={"text": "Change hello to howdy."})
        self.assertEqual(self.file.read_bytes(), b"howdy\r\nworld\r\n")
        step = final["workspace_activity"][0]
        self.assertEqual(step["review"], {"source": "auto", "decision": "approved", "reason": "A scoped wording change."})
        self.assertEqual([e["stage"] for e in self.events if e["type"] == "review"], ["checking", "approved"])
        request = self.api.requests[1]
        self.assertEqual(request["model"], "gpt-6.2-sol")
        self.assertFalse(request["store"])
        self.assertNotIn("tools", request)
        self.assertNotIn("multi_agent", request)
        self.assertNotIn("workspace_review", json.dumps(self.api.requests[-1]["input"]))
        payload = json.loads(request["input"][1]["content"])
        self.assertEqual(payload["user_request"], "Change hello to howdy.")
        self.assertEqual(payload["diff"], step["diff"])
        self.assertEqual(final["usage"]["input_tokens"], 30)
        self.assertEqual(self.app.store.balance()["meter"]["priced"], 3)
        chat = self.app.store.read(start["id"])
        self.assertTrue(chat["workspace_auto_review"])
        self.assertEqual(chat["messages"][-1]["workspace_activity"][0], step)

    def test_rate_limited_reviewer_retries_before_single_file_edit(self):
        self.app.store.set_balance(2)
        self.api.actions = [("edit_file", {"path": "hello.txt", "old": "hello", "new": "howdy"})]
        reviews = []
        def upstream(key, request, stopped):
            if request.get("text", {}).get("format", {}).get("name") == "workspace_review":
                reviews.append(request)
                if len(reviews) == 1:
                    yield "end", {"status": "error", "rate_limit": {"retry_after": .169},
                                  "rejected": True, "note": "Rate limit reached"}
                    return
            yield from provider.events(key, request, stopped)
        self.app.upstream = upstream
        _, result = self.run_turn(workspace_auto_review=True)
        self.assertEqual(result["status"], "done")
        self.assertEqual(len(reviews), 2)
        self.assertEqual(len(result["workspace_activity"]), 1)
        self.assertEqual(self.file.read_bytes(), b"howdy\r\nworld\r\n")
        self.assertEqual([e["type"] for e in self.events if e["type"].startswith("rate_")], ["rate_wait", "rate_resume"])
        meter = self.app.store.balance()["meter"]
        self.assertEqual((meter["priced"], meter["failed"], meter["gaps"]), (3, 1, 0))

    def test_manual_fallback_and_saved_permission_boundary(self):
        self.api.review_answer = {"decision": "manual", "reason": "The replacement changes an unrelated section."}
        self.api.actions = [("edit_file", {"path": "hello.txt", "old": "world", "new": "changed"})]
        def decline(run, proposal):
            self.assertEqual(proposal["review_reason"], self.api.review_answer["reason"])
            self.decide(run, proposal, False)
        start, final = self.run_turn(review=decline, workspace_auto_review=True)
        step = final["workspace_activity"][0]
        self.assertEqual((step["status"], step["review"]["decision"]), ("denied", "denied"))
        self.assertEqual(self.file.read_bytes(), b"hello\r\nworld\r\n")
        with self.assertRaises(urllib.error.HTTPError):
            self.run_turn(cid=start["id"], workspace_terminal=True)
        self.api.requests.clear()
        self.api.review_answer = "invalid response"
        def stop(run, proposal):
            self.assertIn("complete decision", proposal["review_reason"])
            self.post("/api/stop", {"run": run}).close()
        _, final = self.run_turn(review=stop, workspace_auto_review=True)
        self.assertEqual(final["status"], "stopped")
        self.assertEqual(self.file.read_bytes(), b"hello\r\nworld\r\n")

    def test_terminal_review_events_and_saved_output(self):
        subprocess.run(["git", "init", "--quiet"], cwd=self.folder, check=True)
        subprocess.run(["git", "add", "hello.txt"], cwd=self.folder, check=True)
        self.api.actions = [("run_terminal", {"cwd": ".", "argv": ["git", "ls-files"]})]
        start, final = self.run_turn(mode="read", workspace_terminal=True, workspace_auto_review=True)
        step = final["workspace_activity"][0]
        self.assertEqual((step["review"]["source"], step["exit_code"]), ("auto", 0))
        self.assertEqual(step["argv"], self.api.actions[0][1]["argv"])
        self.assertIn("hello.txt", step["output"])
        output = [e for e in self.events if e["type"] == "terminal"]
        self.assertTrue(all(e["id"] == step["id"] for e in output))
        self.assertIn("hello.txt", "".join(e["text"] for e in output))
        chat = self.app.store.read(start["id"])
        self.assertTrue(chat["workspace_terminal"])
        self.assertEqual(chat["messages"][-1]["workspace_activity"][0], step)
        self.assertEqual(len(self.api.requests), 2)  # Read-only command review is local.

        self.api.requests.clear()
        self.api.actions = [("run_terminal", {"cwd": ".", "argv": ["python", "-m", "unittest", "discover"]})]
        def decline(run, proposal):
            self.assertIn("project code", proposal["review_reason"])
            self.decide(run, proposal, False)
        _, final = self.run_turn(workspace_auto_review=True, workspace_terminal=True, review=decline)
        step = final["workspace_activity"][0]
        self.assertEqual((step["status"], step["review"]["decision"]), ("denied", "denied"))
        self.assertFalse(any(e["type"] == "terminal" for e in self.events))

    def test_failed_review_falls_back_and_respects_prior_decline(self):
        edit = ("edit_file", {"path": "hello.txt", "old": "world", "new": "everyone"})
        self.api.actions = [edit, edit]
        checked = []
        def upstream(key, body, stopped):
            if body.get("text", {}).get("format", {}).get("name") == "workspace_review":
                checked.append(body["model"])
                yield "rejected", "This model does not support the requested response format."
            else:
                yield from provider.events(key, body, stopped)
        self.app.upstream = upstream
        reasons = []
        def decline(run, proposal):
            reasons.append(proposal["review_reason"])
            self.decide(run, proposal, False)
        self.app.store.set_balance(2)
        _, final = self.run_turn(review=decline, workspace_auto_review=True)
        self.assertEqual(checked, ["gpt-6.2-sol"])
        self.assertIn("response format", reasons[0])
        self.assertIn("declined an earlier change", reasons[1])
        self.assertEqual([e["status"] for e in final["workspace_activity"]], ["denied", "denied"])
        self.assertEqual(self.app.store.balance()["meter"]["gaps"], 0)
