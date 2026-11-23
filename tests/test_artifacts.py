import base64
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from relay import artifacts
from relay.project_files import ProjectFiles
from relay.server import RelayServer
from test_relay import serving


def workbook():
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as file:
        file.writestr('xl/workbook.xml', f'<workbook xmlns="{ns}" xmlns:r="{rel}"><sheets>'
                      '<sheet name="Sales" r:id="tab"/></sheets></workbook>')
        file.writestr('xl/_rels/workbook.xml.rels',
                      '<Relationships><Relationship Id="tab" Target="worksheets/tab.xml"/></Relationships>')
        file.writestr('xl/worksheets/tab.xml', f'<worksheet xmlns="{ns}"><sheetData><row>'
                      '<c r="A1" t="inlineStr"><is><t>Sales</t></is></c><c r="C1"><v>12</v></c>'
                      '</row></sheetData></worksheet>')
    return output.getvalue()


def legacy_report(store, report):
    """Seed the attachment, local result and enabled switch written by older Relay builds."""
    encoded = base64.b64encode(report).decode()
    source = {"kind": "spreadsheet", "name": "sales.xlsx", "data": f"data:{artifacts.XLSX};base64,{encoded}"}
    cid = store.create("Quarterly sales", messages=[{"role": "user", "text": "Build a report", "files": [source]}])
    with store.db() as db:
        db.execute("ALTER TABLE chats ADD COLUMN data_analysis INTEGER NOT NULL DEFAULT 0")
        db.execute("UPDATE chats SET data_analysis=1 WHERE id=?", (cid,))
    file = {"chat": cid, "run": "previous-run", "name": "report.xlsx", "cited_name": "report.xlsx",
            "source": "Data analysis", "kind": "table", "mime": artifacts.XLSX, "size": len(report)}
    directory = store.chat_folder(cid) / "Results" / file["run"]
    directory.mkdir(parents=True)
    (directory / file["name"]).write_bytes(report)
    store.add(cid, {"role": "assistant", "text": "[Open report](sandbox:/mnt/data/report.xlsx)", "artifacts": [file]})
    return cid, file


class ChatFiles(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        self.home = Path(stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.calls = []

        def fixture(key, body, stopped):
            self.calls.append(body)
            yield "end", {"status": "done", "text": "Fixture answer."}

        self.app = RelayServer(0, self.home / "data", upstream=fixture,
                               key_source=lambda: ("fixture", "test"), codex_home=self.home)
        self.url = stack.enter_context(serving(self.app))

    def post(self, path, data):
        request = urllib.request.Request(self.url + path, data=json.dumps(data).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.read()

    def send(self, **fields):
        body = self.post("/api/respond", fields)
        events = [json.loads(line) for line in body.splitlines()]
        return events[0]["id"], events[-1]

    def get_file(self, kind, cid, file):
        q = urlencode({"chat": cid, "run": file["run"], "name": file["name"]})
        with urllib.request.urlopen(f"{self.url}/api/artifacts/{kind}?{q}", timeout=5) as response:
            return response.read()

    def test_old_workbook_can_be_opened_downloaded_and_branched(self):
        report = workbook()
        cid, file = legacy_report(self.app.store, report)
        self.assertEqual(self.get_file("content", cid, file), report)
        self.assertEqual(json.loads(self.get_file("preview", cid, file)), {"sheet": "Sales", "rows": [["Sales", "", "12"]]})
        with patch.object(artifacts.sys, "platform", "darwin"), patch.object(artifacts.subprocess, "Popen") as opener:
            self.post("/api/artifacts/open", {"chat": cid, "run": file["run"], "name": file["name"], "action": "open"})
            path = self.app.store.chat_folder(cid) / "Results" / file["run"] / file["name"]
            opener.assert_called_once_with(["/usr/bin/open", str(path)])
        branch = self.app.store.fork(cid, 1)
        self.app.store.delete(cid)
        self.assertEqual(self.get_file("content", branch, file), report)
        other = self.app.store.create("A different chat")
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.get_file("content", other, file)
        self.assertEqual(error.exception.code, 404)

    def test_old_switch_and_explicit_request_do_not_enable_hosted_analysis(self):
        report = workbook()
        cid, _ = legacy_report(self.app.store, report)
        csv = {"kind": "text", "name": "notes.csv", "data": "month,sales\nJune,12"}
        for fields in ({"id": cid}, {"temporary": True, "data_analysis": True}):
            _, result = self.send(message={"text": "Read this CSV.", "files": [csv]}, **fields)
            self.assertEqual(result["text"], "Fixture answer.")
            request = self.calls[-1]
            self.assertNotIn("code_interpreter", [tool["type"] for tool in request.get("tools", [])])
            parts = request["input"][-1]["content"]
            self.assertTrue(any(p["type"] == "input_text" and csv["data"] in p["text"] for p in parts))
            self.assertFalse(any(p["type"] == "input_file" for p in parts))
            self.assertNotIn(base64.b64encode(report).decode(), json.dumps(request))

    def test_new_workbook_attachment_reports_csv_alternative(self):
        sheet = {"kind": "spreadsheet", "name": "sales.xlsx", "data": "data:" + artifacts.XLSX + ";base64,QQ=="}
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post("/api/respond", {"data_analysis": True, "message": {"text": "Read this", "files": [sheet]}})
        self.assertEqual(error.exception.code, 400)
        self.assertIn("CSV", json.load(error.exception)["error"])
        self.assertEqual(self.calls, [])

    def test_delete_private_folder_preserves_files_in_each_surviving_branch(self):
        store = self.app.store
        report = workbook()
        cid, file = legacy_report(store, report)
        store.save_draft(cid, "Unsent", 1)
        branch = store.fork(cid, 1)
        descendant = store.fork(branch, 1)
        folder = store.chat_folder(cid)
        (folder / "terminal-output.log").write_text("Generated by a test command.")
        plan = json.loads(self.post("/api/chat/delete-preview", {"id": cid}))
        self.assertEqual(plan, {"folder": str(folder), "project_files": []})
        self.post("/api/chat/delete", {"id": cid, "remove_folder": True})
        self.assertFalse(folder.exists())
        self.assertNotIn(cid, store.drafts())
        self.assertEqual(self.get_file("content", branch, file), report)
        self.post("/api/chat/delete", {"id": branch, "remove_folder": True})
        self.assertFalse(store.chat_folder(branch).exists())
        self.assertEqual(self.get_file("content", descendant, file), report)

    def test_completed_writes_capture_original_project(self):
        store = self.app.store
        cid = store.create("Changes")
        first = self.home / "first"
        first.mkdir()
        file = first / "notes.txt"
        outputs = artifacts.TurnFiles(store, cid, "turn")
        def capture(path, content):
            # Another chat can change the same project before Relay saves its copy.
            file.write_text("Updated by another chat.", encoding="utf-8")
            outputs.workspace(path, content)
        proj = ProjectFiles(first, "edit", threading.Event(), threading.Lock(), on_write=capture)
        proj.execute("create_file", {"path": "notes.txt", "content": "Original content."}, lambda proposal: True)
        proj.execute("edit_file", {"path": "notes.txt", "old": "another", "new": "other"}, lambda proposal: False)
        self.assertEqual(len(outputs.items), 1)
        store.add(cid, {"role": "assistant", "text": "Edited.", "artifacts": outputs.items})
        second = self.home / "second"
        second.mkdir()
        (second / file.name).write_text("A different project.", encoding="utf-8")
        store.set_chat_workspace(cid, "edit", str(second))
        file.unlink()
        self.assertEqual(self.get_file("content", cid, outputs.items[0]), b"Original content.")

    def test_executable_can_be_revealed_but_not_launched(self):
        store = self.app.store
        cid = store.create("Source")
        files = artifacts.TurnFiles(store, cid, "turn")
        files.workspace("build.cmd", b"echo test")
        store.add(cid, {"role": "assistant", "text": "Created the script.", "artifacts": files.items})
        with patch.object(artifacts.sys, "platform", "darwin"), patch.object(artifacts.subprocess, "Popen") as opener:
            body = {"chat": cid, "run": "turn", "name": "build.cmd"}
            with self.assertRaisesRegex(ValueError, "Show in folder"):
                artifacts.open_file(store, body | {"action": "open"})
            opener.assert_not_called()
            artifacts.open_file(store, body | {"action": "reveal"})
            opener.assert_called_once_with(["/usr/bin/open", "-R", str(store.chat_folder(cid) / "Results/turn/build.cmd")])
