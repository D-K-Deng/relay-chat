"""Exercise the desktop WebEngine composer against the local Responses fixture."""
import json
import os
import sys
import tempfile
import textwrap
import time
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
from PySide6.QtCore import QPoint, QUrl, Qt
from PySide6.QtTest import QTest
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from relay import models, provider, terminal
from relay.server import RelayServer
from test_relay import serving
from test_workspace import ResponsesFixture


class FixturePage(QWebEnginePage):
    def chooseFiles(self, mode, previous, mime_types):
        return [str(self.pdf)]


class Browser:
    def __init__(self, pdf):
        self.view = QWebEngineView()
        profile = QWebEngineProfile(self.view)
        self.page = FixturePage(profile, self.view)
        self.page.pdf = pdf
        self.view.setPage(self.page)
        self.view.resize(1240, 840)
        self.view.show()

    def js(self, expression):
        values = []
        self.page.runJavaScript(expression, 0, values.append)
        while not values:
            QTest.qWait(5)
        return values[0]

    def wait(self, expression):
        end = time.monotonic() + 12
        while time.monotonic() < end:
            if self.js(expression):
                return
            QTest.qWait(30)
        raise AssertionError(f"Timed out waiting for {expression}\n{self.js('document.body.innerText')}")

    def click(self, selector):
        element = f"document.querySelector({json.dumps(selector)})"
        self.wait(f"{element} && !{element}.disabled && {element}.getClientRects().length")
        coords = self.js(f"""(() => {{
            const element = {element};
            element.scrollIntoView({{block: 'nearest'}});
            const r = element.getBoundingClientRect();
            return JSON.stringify([r.x + r.width / 2, r.y + r.height / 2]);
        }})()""")
        x, y = json.loads(coords)
        QTest.mouseClick(self.view.focusProxy(), Qt.MouseButton.LeftButton, pos=QPoint(round(x), round(y)))

    def fill(self, selector, value):
        self.js(f"""(() => {{
            const el = document.querySelector({json.dumps(selector)});
            el.value = {json.dumps(value)};
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
        }})()""")


def smoke(root, screenshot=None):
    project = root / "Demo project"
    project.mkdir()
    note = project / "notes.txt"
    note.write_text("Draft.\n", encoding="utf-8")
    pdf = root / "review.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    fake = ThreadingHTTPServer(("127.0.0.1", 0), ResponsesFixture)
    fake.requests = []
    fake.actions = [("read_file", {"path": "notes.txt", "start": 1, "count": 10}),
                    ("edit_file", {"path": "notes.txt", "old": "Draft.", "new": "Reviewed."})]
    app = RelayServer(0, root / "data", key_source=lambda: ("fixture", "test"),
                      codex_home=root / "codex",
                      model_source=lambda key: [models.capabilities(m) for m in ("gpt-5.4-mini", "gpt-6.2-sol")])
    with ExitStack() as stack:
        target = stack.enter_context(serving(fake))
        stack.enter_context(patch.object(provider, "ENDPOINT", target + "/v1/responses"))
        url = stack.enter_context(serving(app))
        browser = Browser(pdf)
        stack.callback(browser.view.close)
        browser.view.load(QUrl(url))
        browser.wait("!!document.querySelector('#refresh-models:not(:disabled)')")
        browser.click("#response-settings")
        browser.click("#model")
        browser.click('[data-value="gpt-6.2-sol"]')
        browser.wait('document.querySelector("#model").textContent.includes("gpt-6.2-sol") && !document.querySelector("#refresh-models").disabled')
        browser.click("#response-settings")
        browser.click("#effort")
        browser.click('[data-value="ultra"]')
        browser.wait('document.querySelector("#effort .choice-value").textContent === "Ultra"')
        browser.click("#tools-toggle")
        browser.click("#workspace")
        browser.click('input[name="mode"][value="edit"]')
        browser.fill("#project-folder", str(project))
        for checkbox in ("#workspace-auto-review", "#workspace-terminal"):
            if browser.js(f'document.querySelector("{checkbox}").checked'):
                browser.click(checkbox)
        browser.click("#project-form [type=submit]")
        browser.wait('document.querySelector("#workspace").textContent.includes("Demo project")')
        browser.click("#attach")
        browser.wait('document.querySelector(".chip")?.textContent.includes("review.pdf")')
        browser.fill("#draft", "Change the status in notes.txt from Draft to Reviewed.")
        browser.click("#send")
        browser.wait('document.querySelector(".project-review pre")?.textContent.includes("+Reviewed.")')
        if screenshot:
            browser.wait('document.querySelector("[data-decision=allow]").getClientRects().length')
            QTest.qWait(350)
            assert browser.view.grab().save(str(screenshot))
        assert note.read_text() == "Draft.\n"
        browser.click('[data-decision="allow"]')
        browser.wait('document.querySelector(".answer")?.textContent.includes("Finished.")')
        assert note.read_text() == "Reviewed.\n"
        assert len(fake.requests) == 3
        first = fake.requests[0]
        assert first["multi_agent"]["enabled"] and first["reasoning"]["effort"] == "xhigh"
        assert any(part.get("type") == "input_file" and part["filename"] == "review.pdf"
                   for part in first["input"][-1]["content"])
        browser.wait('!!document.querySelector("[data-fork]")')
        source_chat = app.store.list_chats()[0]["id"]
        browser.click("[data-fork]")
        browser.wait('document.querySelectorAll("[data-chat]").length === 2')
        assert len(app.store.list_chats()) == 2
        browser.click('[data-action="new"]')
        browser.click(f'[data-chat="{source_chat}"]')
        browser.wait('document.querySelector(".project-activity")?.textContent.includes("2 steps")')
        browser.click(".project-activity > summary")
        browser.click(".project-activity li details > summary")
        browser.wait('document.querySelector(".project-activity .project-diff").getClientRects().length > 0')
        assert browser.js('document.querySelector(".project-activity .project-diff").textContent.includes("+Reviewed.")')
        assert browser.js('document.querySelector(".subagents").textContent.includes("/root/worker")')
        print("WebEngine UI passed: Ultra, PDF, workspace diff approval, chat branch, saved activity.")
        with patch.object(terminal, "host_paths", return_value=[Path(sys.executable).parent]):
            check_terminal_review(browser, app, fake, project, source_chat)


def check_terminal_review(browser, app, fake, project, chat):
    # Hold the fixture test until its first line appears in the live terminal.
    (project / "test_demo.py").write_text(textwrap.dedent("""
        import time
        import unittest
        from pathlib import Path

        class Demo(unittest.TestCase):
            def test_reviewed_note(self):
                Path("started").touch()
                print("Starting fixture check", flush=True)
                deadline = time.monotonic() + 12
                while not Path("continue").exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(Path("continue").exists())
                self.assertEqual(Path("notes.txt").read_text().strip(), "Ready.")
    """), encoding="utf-8")
    fake.requests.clear()
    fake.actions = [
        ("read_file", {"path": "notes.txt", "start": 1, "count": 10}),
        ("edit_file", {"path": "notes.txt", "old": "Reviewed.", "new": "Ready."}),
        ("run_terminal", {"cwd": ".", "argv": ["python", "-m", "unittest", "discover"]}),
    ]
    browser.click("#tools-toggle")
    browser.click("#workspace")
    assert not browser.js('document.getElementById("workspace-auto-review").checked')
    assert not browser.js('document.getElementById("workspace-terminal").checked')
    browser.click("#workspace-auto-review")
    browser.click("#workspace-terminal")
    browser.click("#project-form [type=submit]")
    browser.wait('!document.querySelector("#dialog").open')
    browser.fill("#draft", "Change Reviewed. to Ready. in notes.txt, then run python -m unittest discover.")
    browser.click("#send")
    browser.wait('document.querySelector(".project-review [data-decision=allow]")?.textContent.includes("Run command")')
    assert (project / "notes.txt").read_text().strip() == "Ready."
    assert not (project / "started").exists()
    browser.click('.project-review [data-decision="allow"]')
    browser.wait('document.querySelector(".terminal-output")?.textContent.includes("Starting fixture check")')
    (project / "continue").touch()
    browser.wait('document.querySelectorAll("[data-fork]").length === 2')
    browser.click('[data-action="new"]')
    browser.click(f'[data-chat="{chat}"]')
    browser.wait('document.querySelectorAll(".project-activity").length === 2')
    saved = app.store.read(chat)["messages"][-1]["workspace_activity"]
    edit = next(entry for entry in saved if entry["tool"] == "edit_file")
    command = next(entry for entry in saved if entry["tool"] == "run_terminal")
    assert edit["review"]["source"] == "auto" and edit["review"]["reason"]
    assert command["review"]["source"] == "manual" and command["exit_code"] == 0
    assert "Starting fixture check" in command["output"] and "OK" in command["output"]
    assert browser.js('Array.from(document.querySelectorAll(".terminal-output")).some(el => el.textContent.includes("OK"))')
    browser.click("#tools-toggle")
    browser.click("#workspace")
    assert browser.js('document.getElementById("workspace-auto-review").checked')
    assert browser.js('document.getElementById("workspace-terminal").checked')
    print("WebEngine UI passed: Auto Review, command approval, live output, saved permissions and transcript.")


if __name__ == "__main__":
    application = QApplication(["Relay UI smoke"])
    with tempfile.TemporaryDirectory() as folder:
        smoke(Path(folder), Path(sys.argv[1]) if len(sys.argv) > 1 else None)
