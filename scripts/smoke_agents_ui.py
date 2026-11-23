"""Exercise the subagent sidebar using a local, manually advanced Responses socket."""
import tempfile
from pathlib import Path
from unittest.mock import patch

from smoke_live_ui import MODEL, ResponsesSocket
from smoke_workspace_ui import Browser, QApplication, QUrl, RelayServer, models, serving
from relay import live


def smoke(root):
    app = RelayServer(0, root / "data", key_source=lambda: ("fixture", "test"), codex_home=root / "codex",
                      model_source=lambda key: [models.capabilities(MODEL)])
    app.store.configure({"model": MODEL, "effort": "ultra", "language": "zh-CN"})
    socket = ResponsesSocket([])
    interrupted = ResponsesSocket([])
    worker = {"agent_name": "/root/reviewer"}
    item = {"id": "msg_worker", "type": "message", "phase": "commentary", "agent": worker, "content": []}
    with patch.object(live.RunControl, "_connect", side_effect=[socket, interrupted]), serving(app) as url:
        browser = Browser(None)
        try:
            browser.view.load(QUrl(url))
            browser.wait("document.querySelector('#connection.ready') && !document.querySelector('#refresh-models').disabled")
            browser.fill("#draft", "Review the draft.")
            browser.click("#send")
            browser.wait("document.querySelector('#steer')?.hidden === false")
            cid = app.store.list_chats()[0]["id"]
            socket.emit("response.output_item.added", output_index=0, item=item)
            socket.emit("response.output_text.delta", output_index=0, delta="Reading")
            browser.click("#agent-nav [data-agent-name]")
            browser.wait("document.querySelector('#agent-panel:not([hidden]) .agent-stream').textContent.includes('Reading')")
            socket.emit("response.output_text.delta", output_index=0, delta=" the draft.")
            browser.wait("document.querySelector('.agent-stream').textContent.includes('Reading the draft.')")
            assert browser.js("document.querySelector('.agent-state').textContent === '实时'")
            assert not browser.js("document.querySelector('.answer').textContent.includes('Reading')")
            browser.click('[data-action="new"]')
            browser.wait("document.querySelector('#agent-panel').hidden && document.querySelector('#agent-nav').hidden")
            socket.emit("response.output_text.delta", output_index=0, delta=" Found it.")
            browser.click(f'[data-chat="{cid}"]')
            browser.click("#agent-nav [data-agent-name]")
            browser.wait("document.querySelector('.agent-stream').textContent.includes('Found it.')")
            item["content"] = [{"type": "output_text", "text": "Reading the draft. Found it."}]
            socket.emit("response.output_item.done", output_index=0, item=item)
            root_item = {"id": "msg_root", "type": "message", "phase": "final_answer", "agent": {"agent_name": "/root"},
                         "content": [{"type": "output_text", "text": "Review complete."}]}
            socket.emit("response.completed", response={"id": "fixture-first", "model": MODEL, "output": [item, root_item],
                                                       "usage": {"input_tokens": 10, "output_tokens": 20}})
            browser.wait("!!document.querySelector('[data-fork]') && document.querySelector('.answer').textContent.includes('Review complete.')")
            browser.wait("document.querySelector('.agent-state').textContent === '本轮已结束'")
            assert app.store.read(cid)["messages"][-1]["agent_activity"]["/root/reviewer"]["msg_worker"]["text"] == item["content"][0]["text"]
            browser.click("[data-close-agents]")
            browser.click("[data-fork]")
            browser.wait("document.querySelectorAll('[data-chat]').length === 2")
            browser.click(".subagents [data-agent-name]")
            browser.wait("document.querySelector('#agent-panel:not([hidden]) .agent-stream').textContent.includes('Reading the draft. Found it.')")
            browser.js("window.beforeAgentReload = true")
            browser.view.reload()
            browser.wait("!window.beforeAgentReload && !!document.querySelector('#agent-nav [data-agent-name]')")
            browser.click("#agent-nav [data-agent-name]")
            browser.wait("document.querySelector('#agent-panel:not([hidden]) .agent-stream').textContent.includes('Reading the draft. Found it.')")
            assert not browser.js("document.querySelector('.answer').textContent.includes('Reading')")
            app.store.set_balance(5)
            browser.click('[data-action="new"]')
            browser.fill("#draft", "Check the second draft.")
            browser.click("#send")
            browser.wait("document.querySelector('#steer')?.hidden === false")
            interrupted.emit("response.output_item.added", output_index=0, item=item | {"content": []})
            interrupted.emit("response.output_text.delta", output_index=0, delta="Part of the review")
            browser.click("#agent-nav [data-agent-name]")
            browser.wait("document.querySelector('.agent-stream').textContent.includes('Part of the review')")
            browser.click("#send")
            browser.wait("document.querySelector('.agent-state').textContent === '本轮已停止'")
            browser.wait("document.querySelector('.msg.assistant .meta').textContent.includes('估算用量')")
            browser.click("#balance-pill")
            browser.wait("document.querySelector('#balance-live .form-help')?.textContent.includes('来自 1 次')")
            meter = app.store.balance()["meter"]
            assert 0 < meter["estimated_spend"] == meter["spent"] and meter["remaining"] < 5
            displayed = browser.js("document.querySelector('#balance-live .form-help').textContent")
            assert f'{meter["spent"]:.8f}'.rstrip('0') in displayed, displayed
            print("Qt subagent sidebar passed: live/background output, saved fork, reload, stop and local estimated spend.")
        finally:
            browser.view.close()


if __name__ == "__main__":
    application = QApplication(["Relay subagent smoke"])
    with tempfile.TemporaryDirectory() as temporary:
        smoke(Path(temporary))
