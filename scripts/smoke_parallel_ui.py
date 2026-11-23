"""Exercise overlapping chats, a background command and chat-scoped stop in Qt."""
import json
import tempfile
import textwrap
import threading
from pathlib import Path

from smoke_workspace_ui import Browser, QApplication, QUrl, RelayServer, models, serving


def smoke(root):
    finish_second = threading.Event()

    def provide(key, body, stopped):
        last = body["input"][-1]
        if last.get("type") == "function_call_output":
            yield "end", {"status": "done", "text": "Project tests finished."}
            return
        question = last["content"][-1]["text"]
        if question == "Run project tests":
            call = {"type": "function_call", "name": "run_terminal", "call_id": "fixture-command",
                    "arguments": json.dumps({"cwd": ".", "argv": ["python", "-m", "unittest", "discover"]})}
            yield "end", {"status": "done", "output_items": [call]}
        else:
            yield "delta", "Working on " + question
            if question == "Write release notes":
                if finish_second.wait(30) and not stopped.is_set():
                    yield "end", {"status": "done", "text": "Release notes ready."}
            else:
                stopped.wait(30)

    app = RelayServer(0, root / "data", upstream=provide, key_source=lambda: ("fixture", "test"),
                      codex_home=root / "codex", model_source=lambda key: [models.capabilities("gpt-5.4-mini")])
    with serving(app) as url:
        browser = Browser(None)
        try:
            browser.view.load(QUrl(url))
            browser.wait("!!document.querySelector('#connection.ready') && !document.querySelector('#refresh-models').disabled")

            # A slow first acknowledgement must not bind its new ID to the second composer.
            browser.js("""(() => {
                const fetch = window.fetch.bind(window);
                let first = true;
                window.fetch = async (...args) => {
                    if (args[0] === '/api/respond' && first) {
                        first = false;
                        const pending = fetch(...args);
                        await new Promise(resolve => window.releaseFirst = resolve);
                        return pending;
                    }
                    return fetch(...args);
                };
            })()""")
            browser.fill("#draft", "Run project tests")
            browser.click("#send")
            browser.wait("typeof window.releaseFirst === 'function'")
            browser.click('[data-action="new"]')
            browser.wait("document.querySelector('#draft').value === '' && !document.querySelector('#draft').readOnly")
            browser.fill("#draft", "Write release notes")
            browser.click("#send")
            browser.wait("document.querySelector('.answer')?.textContent.includes('Working on Write release notes')")
            browser.js("window.releaseFirst()")
            ids = {chat["title"]: chat["id"] for chat in app.store.list_chats()}
            first, second = ids["Run project tests"], ids["Write release notes"]
            project = app.store.chat_folder(first)
            (project / "test_review.py").write_text(textwrap.dedent("""
                import time
                import unittest
                from pathlib import Path

                class Review(unittest.TestCase):
                    def test_output(self):
                        print('Starting tests in A', flush=True)
                        for signal in ('next', 'finish'):
                            deadline = time.monotonic() + 20
                            while not Path(signal).exists() and time.monotonic() < deadline:
                                time.sleep(.03)
                            self.assertTrue(Path(signal).exists())
                            print('Received ' + signal, flush=True)
            """), encoding="utf-8")

            def row(cid):
                return f'[data-chat="{cid}"]'

            def badge(selector, status):
                browser.wait(f"!!document.querySelector('{selector} [data-status={status}]')")

            badge(row(first), "review")
            assert browser.js("location.hash") == "#chat/" + second
            assert not browser.js("!!document.querySelector('.project-review')")
            browser.click(row(first))
            browser.click('[data-decision="allow"]')
            browser.wait("document.querySelector('.terminal-live')?.textContent.includes('Starting tests in A')")
            browser.click(row(second))
            (project / "next").touch()
            browser.click('[data-action="temp"]')
            browser.wait("location.hash === '#temporary'")
            browser.fill("#draft", "Temporary stop")
            browser.click("#send")
            browser.wait("document.querySelector('.answer')?.textContent.includes('Working on Temporary stop')")
            browser.click(row(first))
            badge("#nav-temp", "running")
            browser.wait("document.querySelector('.terminal-live')?.textContent.includes('Received next')")
            finish_second.set()
            badge(row(second), "done")
            assert browser.js("document.querySelector('#messages').innerText.includes('Release notes ready')") is False
            browser.click('#nav-temp')
            browser.wait("location.hash === '#temporary'")
            browser.click('#send')
            browser.wait("document.querySelector('.notice')?.textContent.includes('stopped')")
            browser.click(row(second))
            browser.wait("document.querySelector('.answer')?.textContent.includes('Release notes ready')")
            assert not browser.js(f"!!document.querySelector('{row(second)} [data-status=done]')")
            assert not browser.js("!!document.querySelector('.terminal-live')")
            browser.fill("#draft", "Keep this unsent follow-up")
            (project / "finish").touch()
            badge(row(first), "done")
            assert browser.js("document.querySelector('#draft').value") == "Keep this unsent follow-up"
            browser.click(row(first))
            browser.wait("document.querySelector('.project-activity')?.textContent.includes('Received finish')")
            assert browser.js("document.querySelector('.answer').innerText") == "Project tests finished."
            activity = app.store.read(first)["messages"][-1]["workspace_activity"][0]
            assert activity["exit_code"] == 0 and activity["review"]["source"] == "manual"
            assert app.store.read(second)["messages"][-1]["text"] == "Release notes ready."
            assert len(app.store.list_chats()) == 2
            check_limit(browser)
            print("Qt parallel chats passed: delayed start, separate approval, background output, completion and stop.")
        finally:
            finish_second.set()
            browser.view.close()


def check_limit(browser):
    active = []
    for n in range(4):
        browser.click('[data-action="new"]')
        browser.wait("location.hash === '' && document.querySelector('#draft').value === ''")
        browser.fill("#draft", f"Parallel {n}")
        browser.click("#send")
        browser.wait(f"document.querySelector('.answer')?.textContent.includes('Working on Parallel {n}')")
        active.append('[data-chat="' + browser.js("location.hash.slice(6)") + '"]')
    browser.click('[data-action="new"]')
    browser.wait("location.hash === '' && document.querySelector('#draft').value === ''")
    browser.fill("#draft", "Fifth chat")
    browser.click("#send")
    browser.wait("!!document.querySelector('#toast.on')")
    assert browser.js("document.querySelector('#draft').value") == "Fifth chat"
    assert browser.js("document.querySelectorAll('#history [data-status=running]').length") == 4
    for selector in active:
        browser.click(selector)
        browser.wait(f"document.querySelector('{selector}').classList.contains('active')")
        browser.click("#send")
        browser.wait("document.querySelector('.notice')?.textContent.includes('stopped')")
    browser.click('[data-action="new"]')
    browser.wait("document.querySelector('#draft').value === 'Fifth chat'")
    browser.click("#send")
    browser.wait("document.querySelector('.answer')?.textContent.includes('Working on Fifth chat')")
    browser.click("#send")
    browser.wait("document.querySelector('.notice')?.textContent.includes('stopped')")


if __name__ == "__main__":
    application = QApplication(["Relay parallel chat smoke"])
    with tempfile.TemporaryDirectory() as folder:
        smoke(Path(folder))
