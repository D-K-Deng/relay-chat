"""Exercise steering, checkpoint display and replay in a desktop chat with a fixture socket."""
import json
import queue
import tempfile
from pathlib import Path
from unittest.mock import patch

from smoke_workspace_ui import Browser, QApplication, QUrl, RelayServer, models, serving
from relay import live, provider


MODEL = "gpt-6.2-sol"
UPDATE = "Focus on macOS."
CHECKPOINT = {"type": "compaction", "id": "cmp_fixture", "encrypted_content": "opaque-fixture"}


class ResponsesSocket:
    def __init__(self, requests):
        self.events = queue.Queue()
        self.requests = requests

    def emit(self, event_type, **data):
        self.events.put(json.dumps({"type": event_type, **data}))

    def reply(self, text, items):
        self.emit("response.output_text.delta", delta=text)
        message = {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}
        self.emit("response.completed", response={"id": "fixture-finished", "model": MODEL,
                  "output": [*items, message], "usage": {"input_tokens": 80, "output_tokens": 11}})

    def send(self, data):
        request = json.loads(data)
        self.requests.append(request)
        if request["type"] == "response.steer":
            self.emit("response.steer.accepted", steer={"id": "update-1"}, sequence_number=1)
            self.emit("response.incomplete", response={"id": "fixture-first", "output": [],
                      "incomplete_details": {"reason": "steered"}, "usage": {"input_tokens": 48, "output_tokens": 2}})
            self.emit("response.created", response={"id": "fixture-second"})
            self.emit("response.output_item.added", item=CHECKPOINT)
            self.reply("The macOS changes are ready.", [CHECKPOINT])
        elif len(self.requests) == 1:
            self.emit("response.created", response={"id": "fixture-first"})
        else:
            self.emit("response.created", response={"id": "fixture-followup"})
            self.reply("Continuing from the saved update.", [])

    def recv(self):
        return self.events.get(timeout=18)

    def shutdown(self):
        self.events.put("")


def smoke(root):
    requests = []
    app = RelayServer(0, root / "data", key_source=lambda: ("fixture", "test"), codex_home=root / "codex",
                      model_source=lambda key: [models.capabilities(MODEL)])
    app.store.configure({"model": MODEL})
    app.context_limits[MODEL] = 4096
    history = "Previous desktop release notes. " * 400
    with patch.object(live.RunControl, "_connect", side_effect=lambda key, body: ResponsesSocket(requests)), serving(app) as url:
        browser = Browser(None)
        try:
            browser.view.load(QUrl(url))
            browser.wait("document.querySelector('#connection.ready') && !document.querySelector('#refresh-models').disabled")
            browser.fill("#draft", "Work on the desktop build.\n" + history)
            browser.click("#send")
            browser.wait("document.querySelector('#steer')?.hidden === false && !document.querySelector('#draft').readOnly")
            cid = app.store.list_chats()[0]["id"]
            # Hold the acknowledgement while a different composer has the same text.
            browser.js("""(() => {
                const fetch = window.fetch.bind(window);
                window.fetch = async (...args) => {
                    const result = fetch(...args);
                    if (args[0] === '/api/steer')
                        await new Promise(resolve => window.releaseUpdate = resolve);
                    return result;
                };
            })()""")
            browser.fill("#draft", UPDATE)
            browser.click("#steer")
            browser.wait("typeof window.releaseUpdate === 'function'")
            browser.click('[data-action="new"]')
            browser.wait("location.hash === '' && document.querySelector('#crumb').textContent === 'New chat'")
            browser.fill("#draft", UPDATE)
            browser.js("window.releaseUpdate()")
            browser.click(f'[data-chat="{cid}"]')
            browser.wait("document.querySelector('[data-fork]') && document.querySelector('.answer')?.textContent.includes('macOS changes are ready')")
            browser.wait("document.querySelector('#draft').value === ''")
            browser.wait("document.querySelector('#context-usage:not([hidden])')?.textContent.includes('Compacted')")
            assert browser.js("document.querySelector('.steer-update')?.textContent.includes('Focus on macOS.')")
            assert requests[1] == {"type": "response.steer", "previous_response_id": "fixture-first", "input": UPDATE}
            assert requests[0]["context_management"][0]["type"] == "compaction"
            saved = app.store.read(cid)["messages"][-1]
            assert saved["steers"] == [{"text": UPDATE, "status": "applied"}]
            assert saved["compaction"]["items"][0] == CHECKPOINT
            browser.click('[data-action="new"]')
            browser.wait("location.hash === '' && document.querySelector('#draft').value === " + json.dumps(UPDATE))
            browser.click(f'[data-chat="{cid}"]')
            browser.wait("document.querySelector('.steer-update')?.textContent.includes('Focus on macOS.')")
            assert browser.js("document.querySelector('#context-usage:not([hidden])').textContent.includes('Compacted')")
            assert not browser.js("document.body.innerText.includes('opaque-fixture')")
            browser.fill("#draft", "Continue with that plan")
            browser.click("#send")
            browser.wait("document.querySelectorAll('[data-fork]').length === 2 && document.querySelectorAll('.answer')[1]?.textContent.includes('Continuing from the saved update')")
            assert CHECKPOINT in requests[-1]["input"]
            assert "Previous desktop release notes." not in str(requests[-1]["input"])
            assert history.strip() in app.store.read(cid)["messages"][0]["text"]
            assert requests[-1]["input"][-1]["content"][0]["text"] == "Continue with that plan"

            def interrupted(key, body, stopped):
                yield "delta", "Partial answer"
                yield from provider.Decoder(body).feed({"type": "error", "status": 500, "request_id": "req_fixture",
                    "error": {"code": "server_error", "message": "Fixture interrupted after generation"}})

            app.upstream = interrupted
            app.store.set_balance(5)
            browser.fill("#draft", "Exercise a failure")
            browser.click("#send")
            browser.wait("document.querySelector('.notice.bad')?.textContent.includes('req_fixture')")
            assert browser.js("document.querySelector('.notice.bad').textContent.includes('Fixture interrupted after generation')")
            assert browser.js("document.querySelector('.msg.assistant:last-child .meta').textContent.includes('Estimated usage')")
            browser.click("#balance-pill")
            browser.wait("document.querySelector('#balance-live')?.textContent.includes('Failed requests')")
            assert browser.js("document.querySelector('#balance-live').textContent.includes('estimated locally for 1 interrupted')")
            print("Qt live run passed: steering, checkpoint replay, a partial failure with diagnostics, and estimated spend.")
        finally:
            browser.view.close()


if __name__ == "__main__":
    application = QApplication(["Relay live run smoke"])
    with tempfile.TemporaryDirectory() as temporary:
        smoke(Path(temporary))
