"""Hold the composer microphone using Chromium's synthetic audio device."""
import json
import os
import tempfile
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --use-fake-device-for-media-stream"
from smoke_workspace_ui import Browser, QApplication, QPoint, QTest, Qt, QUrl, RelayServer, models, serving
from relay import dictation
from relay.windows_desktop import allow_microphone
from test_dictation import AudioFixture


def smoke(root):
    audio = ThreadingHTTPServer(("127.0.0.1", 0), AudioFixture)
    audio.calls = []
    audio.status = 503
    audio.answer = {"error": {"message": "Try again"}}
    app = RelayServer(0, root / "data", key_source=lambda: ("fixture", "test"), codex_home=root / "codex",
                      model_source=lambda key: [models.capabilities("gpt-5.4-mini")])
    with ExitStack() as stack:
        host = stack.enter_context(serving(audio))
        stack.enter_context(patch.object(dictation, "ENDPOINT", host + "/v1/audio/transcriptions"))
        url = QUrl(stack.enter_context(serving(app)))
        browser = Browser(None)
        stack.callback(browser.view.close)
        browser.page.permissionRequested.connect(lambda permission: allow_microphone(permission, url))
        browser.view.load(url)
        browser.wait("document.querySelector('#connection.ready') && !document.querySelector('#dictate').disabled")
        browser.fill("#draft", "已有文字。")
        x, y = json.loads(browser.js("""(() => {
            const r = document.querySelector('#dictate').getBoundingClientRect();
            return JSON.stringify([r.x + r.width/2, r.y + r.height/2]);
        })()"""))
        click = dict(pos=QPoint(round(x), round(y)))
        widget = browser.view.focusProxy()

        def hold():
            QTest.mousePress(widget, Qt.MouseButton.LeftButton, **click)
            browser.wait("document.querySelector('#dictate').hasAttribute('data-recording')")
            QTest.qWait(650)

        hold()
        QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, **click)
        browser.wait("document.querySelector('#dictation-status button')?.textContent === 'Retry transcription'")
        assert browser.js("document.querySelector('#draft').value") == "已有文字。"
        audio.status = 200
        audio.answer = {"text": "请继续。", "usage": {"type": "duration", "seconds": 1}}
        browser.click("#dictation-status button")
        browser.wait("document.querySelector('#draft').value === '已有文字。请继续。'")
        assert not browser.js("document.querySelector('#send').disabled")
        assert len(audio.calls) == 2
        first, retried = [call[2]["file"].get_payload(decode=True) for call in audio.calls]
        assert len(first) > 100 and first == retried
        assert app.store.list_chats() == []

        hold()
        QTest.keyClick(widget, Qt.Key.Key_Escape)
        QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, **click)
        QTest.qWait(350)
        assert len(audio.calls) == 2
        assert browser.js("document.querySelector('#draft').value") == "已有文字。请继续。"
        assert app.store.drafts() == {"new": "已有文字。请继续。"}
        print("Qt microphone passed: real recording from synthetic device, release, retry, editable draft and cancel.")


if __name__ == "__main__":
    application = QApplication(["Relay dictation smoke"])
    with tempfile.TemporaryDirectory() as folder:
        smoke(Path(folder))
