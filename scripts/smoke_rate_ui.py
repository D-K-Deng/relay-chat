"""Exercise rate-limit recovery, stop and historical diagnostics in the desktop UI."""
import tempfile
from pathlib import Path

from smoke_workspace_ui import Browser, QApplication, QUrl, RelayServer, models, serving
from relay import provider


MODEL = "gpt-6.2-astra"
DETAIL = (f"Rate limit reached for {MODEL} (for limit {MODEL}-ultrafast) in organization org-fixture "
          "on tokens per min (TPM): Limit 1000000, Used 953051, Requested 55658.")


def smoke(root):
    requests = []

    def upstream(key, body, stopped):
        question = next((text for text in ("Stop during backoff", "Resume after backoff", "Return the limit immediately")
                         if text in str(body["input"])), "Continue")
        requests.append(question)
        attempt = requests.count(question)
        delay = None
        if question == "Stop during backoff":
            delay = 5 if attempt == 1 else 15
        elif question == "Return the limit immediately" or (question == "Resume after backoff" and attempt == 1):
            delay = 1
        if delay:
            yield "steering", {"available": True}
            if attempt == 1:
                yield "delta", "A partial reply. "
            decoder = provider.Decoder(body)
            yield from decoder.feed({"type": "response.created", "response": {"id": "resp_fixture"}})
            yield from decoder.feed({"type": "error", "status": 429, "error": {
                "code": "rate_limit_exceeded", "message": DETAIL + f" Please try again in {delay}s."}})
        else:
            yield "delta", "Continued."
            yield "end", {"status": "done", "text": "Continued.", "usage": {"input_tokens": 25, "output_tokens": 3}}

    app = RelayServer(0, root / "data", upstream=upstream, key_source=lambda: ("fixture", "test"),
                      codex_home=root / "codex", model_source=lambda key: [models.capabilities(MODEL)])
    app.store.configure({"model": MODEL, "language": "zh-CN", "effort": "ultra", "service_tier": "ultrafast"})
    historical = "The rate limit was reached. Wait a moment before retrying.\n" + DETAIL + "\nrate_limit_exceeded · request_id=req_fixture"
    def saved(title, note):
        return app.store.create(title, messages=[{"role": "user", "text": title}, {"role": "assistant", "model": MODEL,
            "text": "Earlier partial", "status": "error", "note": note}])
    older = saved("Historical rate error", historical)
    quota = saved("Exhausted credits", "API credits exhausted. Check the balance, expiry, and organization in OpenAI Billing.")
    oversized = saved("Oversized request", "The rate limit was reached. Wait a moment before retrying.\nRequest too large for this model.\nrate_limit_exceeded")
    with serving(app) as url:
        browser = Browser(None)
        try:
            browser.view.load(QUrl(url))
            browser.wait("document.querySelector('#connection.ready') && !document.querySelector('#refresh-models').disabled")
            browser.click('[data-action="settings"]')
            browser.wait("document.querySelector('[name=auto_wait_rate_limits]')?.checked")
            browser.click("#close-modal")
            browser.wait("!document.querySelector('#dialog').open")
            browser.fill("#draft", "Stop during backoff")
            browser.click("#send")
            browser.wait("document.querySelector('.answer')?.textContent.includes('A partial reply.') && document.querySelector('#steer').hidden")
            assert not browser.js("!!document.querySelector('.rate-progress, [data-status=waiting]')")
            # The first wait lasts five seconds. The cumulative wait crosses eight during the second.
            browser.wait("document.querySelector('.rate-progress')?.textContent.includes('等待 API 容量')")
            cid = app.store.list_chats()[0]["id"]
            assert browser.js("document.querySelector('.answer').textContent.includes('A partial reply.')")
            assert browser.js("document.querySelector('#steer').hidden && !document.querySelector('#send').disabled")
            browser.click('[data-action="new"]')
            browser.wait("!document.querySelector('.rate-progress') && !!document.querySelector('#history [data-status=waiting]')")
            browser.click(f'[data-chat="{cid}"]')
            browser.wait("document.querySelector('.rate-progress')?.textContent.includes('等待 API 容量')")
            browser.click("#send")
            browser.wait("document.querySelector('.notice')?.textContent.includes('已停止')")
            assert len(requests) == 2

            browser.click('[data-action="new"]')
            browser.wait("!location.hash && !document.querySelector('#draft').readOnly && !document.querySelector('.notice')")
            browser.js("""(() => {
                window.shortWaitShown = false;
                window.waitObserver = new MutationObserver(() => {
                    if (document.querySelector('.rate-progress, [data-status=waiting]')) window.shortWaitShown = true;
                });
                waitObserver.observe(document.querySelector('#layout'), {childList: true, subtree: true});
            })()""")
            browser.fill("#draft", "Resume after backoff")
            browser.click("#send")
            browser.wait("document.querySelector('.answer')?.textContent.includes('A partial reply. Continued.') && !document.querySelector('.answer .typing')")
            browser.js("waitObserver.disconnect()")
            assert not browser.js("shortWaitShown || !!document.querySelector('.notice')")

            browser.click(f'[data-chat="{older}"]')
            browser.wait("document.querySelector('.notice.bad')?.textContent.includes('上限 1,000,000')")
            assert browser.js("document.querySelector('.notice.bad').textContent.includes('953,051')")
            browser.click(".rate-details summary")
            assert browser.js("document.querySelector('.rate-details').textContent.includes('req_fixture') && !document.body.innerText.includes('org-fixture')")
            browser.click('[data-action="continue"]')
            browser.wait("document.querySelectorAll('.msg.assistant').length === 2 && document.querySelector('.msg.assistant:last-child .answer').textContent.includes('Continued.') && !document.querySelector('.answer .typing')")
            assert len(app.store.read(older)["messages"]) == 4
            browser.click(f'[data-chat="{quota}"]')
            browser.wait("document.querySelector('.notice.bad')?.textContent.includes('API 额度已用尽')")
            assert not browser.js("!!document.querySelector('.rate-details, [data-action=continue]')")
            browser.click(f'[data-chat="{oversized}"]')
            browser.wait("document.querySelector('.notice.bad')?.textContent.includes('单次请求')")
            assert not browser.js("!!document.querySelector('[data-action=continue]')")
            app.store.configure({"language": "en"})
            browser.view.reload()
            browser.wait("document.documentElement.lang === 'en' && !!document.querySelector('.notice.bad')")
            browser.click(f'[data-chat="{older}"]')
            browser.wait("document.querySelector('.notice.bad')?.textContent.includes('Token-per-minute limit reached')")
            browser.click('[data-action="settings"]')
            browser.wait("document.querySelector('[name=auto_wait_rate_limits]')?.checked")
            browser.click('[name=auto_wait_rate_limits]')
            browser.click('#settings-form button[type=submit]')
            browser.wait("!document.querySelector('#dialog').open")
            assert app.store.settings()["auto_wait_rate_limits"] is False
            browser.js("window.previousPage = true")
            browser.view.load(QUrl(url + "/?restart=1"))
            browser.wait("!window.previousPage && !location.hash && document.querySelector('#connection.ready') && !document.querySelector('#refresh-models').disabled")
            browser.click('[data-action="settings"]')
            browser.wait("!!document.querySelector('[name=auto_wait_rate_limits]:not(:checked)')")
            browser.click("#close-modal")
            browser.wait("!document.querySelector('#dialog').open")
            browser.js("""(() => {
                const fetch = window.fetch.bind(window);
                window.fetch = (...args) => {
                    if (args[0] === '/api/respond') window.sentWaitPreference = JSON.parse(args[1].body).model_settings.auto_wait_rate_limits;
                    return fetch(...args);
                };
            })()""")
            browser.fill("#draft", "Return the limit immediately")
            browser.click("#send")
            browser.wait("document.querySelector('.notice.bad')?.textContent.includes('Token-per-minute limit reached') && !!document.querySelector('[data-action=continue]')")
            assert browser.js("window.sentWaitPreference === false")
            assert requests.count("Return the limit immediately") == 1
            print("Qt rate-limit UI passed: silent recovery, cumulative wait notice, scoped stop, saved switch and opt-out.")
        finally:
            browser.view.close()


if __name__ == "__main__":
    application = QApplication(["Relay rate limit smoke"])
    with tempfile.TemporaryDirectory() as directory:
        smoke(Path(directory))
