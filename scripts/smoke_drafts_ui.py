"""Exercise draft recovery across fresh desktop processes and loopback ports."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from smoke_workspace_ui import Browser, QApplication, QUrl, RelayServer, models, serving

NEW_TEXT = "Start a new question.\n第二行。"
SAVED_TEXT = "Follow up here.\n保留换行。"


def session(root, stage):
    def answer(key, body, stopped):
        yield "end", {"status": "done", "text": "Fixture answer."}

    server = RelayServer(0, root / "data", upstream=answer, key_source=lambda: ("fixture", "test"),
                         codex_home=root / "codex", model_source=lambda key: [models.capabilities("gpt-5.4-mini")])
    cid = server.store.create("Saved conversation") if stage == "write" else server.store.list_chats()[0]["id"]
    attachment = root / "question.txt"
    attachment.write_text("Window-only attachment.", encoding="utf-8")
    with serving(server) as url:
        browser = Browser(attachment)
        browser.view.load(QUrl(url))
        try:
            browser.wait("!!document.querySelector('#connection.ready')")
            def expect(text):
                browser.wait("document.querySelector('#draft').value === " + json.dumps(text))

            if stage == "write":
                browser.fill("#draft", NEW_TEXT)
                browser.click("#attach")
                browser.wait("!!document.querySelector('.chip')")
                browser.click('[data-action="temp"]')
                browser.fill("#draft", "Private draft")
                browser.click(f'[data-chat="{cid}"]')
                browser.wait(f"location.hash === '#chat/{cid}'")
                browser.fill("#draft", SAVED_TEXT)
                browser.click('[data-action="new"]')
                expect(NEW_TEXT)
                browser.wait("!!document.querySelector('.chip')")
                browser.click('[data-action="temp"]')
                expect("Private draft")
            elif stage == "restore":
                expect(NEW_TEXT)
                assert not browser.js("!!document.querySelector('.chip')")
                browser.click('[data-action="temp"]')
                expect("")
                browser.click(f'[data-chat="{cid}"]')
                expect(SAVED_TEXT)
                browser.click("#send")
                browser.wait("!!document.querySelector('[data-fork]')")
                assert server.store.read(cid)["messages"][0]["text"] == SAVED_TEXT
                browser.click('[data-action="new"]')
                expect(NEW_TEXT)
                browser.fill("#draft", "")
            else:
                expect("")
                browser.click(f'[data-chat="{cid}"]')
                browser.wait(f"location.hash === '#chat/{cid}'")
                expect("")
                assert server.store.drafts() == {}
        finally:
            browser.view.close()


if __name__ == "__main__":
    if len(sys.argv) == 3:
        application = QApplication(["Relay draft smoke"])
        session(Path(sys.argv[1]), sys.argv[2])
    else:
        with tempfile.TemporaryDirectory() as folder:
            for stage in ("write", "restore", "verify-cleared"):
                subprocess.run([sys.executable, __file__, folder, stage], check=True)
        print("Desktop drafts passed: restart, chat switching, temporary privacy and clearing after send or edit.")
