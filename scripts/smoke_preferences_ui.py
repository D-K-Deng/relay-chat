"""Check saved composer choices across fresh desktop processes using one temporary database."""
import subprocess
import sys
import tempfile
from pathlib import Path

from smoke_workspace_ui import Browser, QApplication, QTest, Qt, QUrl, RelayServer, models, serving


MODEL = "gpt-6.2-sol"
CATALOG = ["gpt-5.4-mini", MODEL, "o3-mini"]
READY = "!!document.querySelector('#refresh-models:not(:disabled)')"


def choose(browser, control, value):
    browser.click("#response-settings")
    browser.click("#" + control)
    browser.click(f'[data-value="{value}"]')
    browser.wait(READY)


def expect_switch(browser, control, enabled):
    pressed = "true" if enabled else "false"
    browser.wait(f'''{READY} && !!document.querySelector('#{control}[aria-pressed="{pressed}"]:not(:disabled)')''')


def expect_speed_icon(browser, tier, label):
    browser.wait(f"""(() => {{
        const button = document.querySelector('#response-settings');
        const icon = button.querySelector('.speed-indicator[data-tier="{tier}"] svg');
        if (!icon || !button.getAttribute('aria-label').includes('{label}')) return false;
        const r = icon.getBoundingClientRect(), b = button.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && r.left >= b.left && r.right <= b.right &&
            r.top >= b.top && r.bottom <= b.bottom && button.contains(document.elementFromPoint(
                r.left + r.width / 2, r.top + r.height / 2));
    }})()""")


def check_toolbar_layout(browser, speed_label):
    browser.fill("#draft", "Layout check")
    for width in (320, 390, 1240):
        browser.view.resize(width, 840)
        browser.wait(f"innerWidth === {width}")
        expect_speed_icon(browser, "ultrafast", speed_label)
        browser.wait("""(() => {
            const controls = ['attach', 'dictate', 'tools-toggle', 'response-settings', 'send']
                .map(id => document.getElementById(id));
            const rects = controls.map(el => el.getBoundingClientRect());
            const centers = rects.map(r => r.top + r.height / 2);
            return Math.max(...centers) - Math.min(...centers) < 3 &&
                document.documentElement.scrollWidth <= innerWidth &&
                rects.every((r, i) => r.width > 0 && r.left >= 0 && r.right <= innerWidth &&
                    controls[i].contains(document.elementFromPoint(r.x + r.width / 2, centers[i])));
        })()""")
        assert not browser.js("document.getElementById('data-analysis') !== null")
        browser.click("#response-settings")
        browser.click("#effort")
        browser.wait('!!document.querySelector("#choice-options [data-value=ultra]")?.getClientRects().length')
        browser.click("#choice-back")
        browser.wait('!!document.querySelector("#model").getClientRects().length')
        QTest.keyClick(browser.view.focusProxy(), Qt.Key.Key_Escape)
        browser.click("#tools-toggle")
        browser.wait("""['web-search', 'auto-review', 'terminal-commands', 'workspace'].every(id => {
            const el = document.getElementById(id);
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.left >= 0 && r.right <= innerWidth &&
                el.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
        })""")
        QTest.keyClick(browser.view.focusProxy(), Qt.Key.Key_Escape)
        browser.wait("document.querySelector('#tools-menu').hidden")
    browser.fill("#draft", "")


def language(browser, value):
    browser.click('[data-action="settings"]')
    browser.wait("!!document.getElementById('ui-language')")
    browser.js(f"""(() => {{
        const select = document.querySelector('#ui-language');
        select.value = '{value}';
        select.dispatchEvent(new Event('change', {{bubbles: true}}));
    }})()""")
    browser.wait(f"document.documentElement.lang === '{value}'")
    heading = "设置" if value == "zh-CN" else "Settings"
    browser.wait(f"document.querySelector('#modal-title').textContent === '{heading}'")
    browser.click("#close-modal")
    browser.wait("!document.querySelector('#dialog').open")


def session(root, stage):
    catalog = CATALOG.copy()
    if stage == "restore":
        catalog.remove(MODEL)
    requests = []

    def fixture(key, body, stopped):
        requests.append(body)
        yield "end", {"status": "done", "text": "Fixture answer."}

    server = RelayServer(0, root / "data", upstream=fixture, key_source=lambda: ("fixture", "test"),
                         codex_home=root / "codex", model_source=lambda key: [models.capabilities(m) for m in catalog])
    if stage == "choose":
        imported = server.store.create("New chat", source="codex", messages=[
            {"role": "user", "text": "Settings", "files": [{"kind": "text", "name": "Search", "data": "Example"}]},
            {"role": "assistant", "text": "Terminal commands"},
        ])
    with serving(server) as url:
        browser = Browser(None)
        try:
            browser.view.load(QUrl(url))
            browser.wait(READY)
            if stage == "choose":
                for control in ("web-search", "auto-review", "terminal-commands"):
                    expect_switch(browser, control, True)
                artwork = set()
                for tier, label in (("default", "Standard"), ("fast", "Fast"), ("flex", "Flex")):
                    choose(browser, "service-tier", tier)
                    expect_speed_icon(browser, tier, label)
                    artwork.add(browser.js("document.querySelector('.speed-indicator svg').innerHTML"))
                choose(browser, "model", MODEL)
                choose(browser, "effort", "ultra")
                choose(browser, "service-tier", "ultrafast")
                expect_speed_icon(browser, "ultrafast", "Ultrafast")
                artwork.add(browser.js("document.querySelector('.speed-indicator svg').innerHTML"))
                assert len(artwork) == 4, "Each selectable speed should have its own icon"
                check_toolbar_layout(browser, "Ultrafast")
                language(browser, "zh-CN")
                browser.wait("document.querySelector('[data-action=new] span').textContent === '新建聊天'")
                check_toolbar_layout(browser, "极速")
                browser.click(f'[data-chat="{imported}"]')
                browser.wait("document.querySelector('.msg.assistant .answer')?.textContent.trim() === 'Terminal commands'")
                assert browser.js("document.querySelector('#crumb').textContent") == "New chat"
                assert browser.js("document.querySelector('.msg.user .bubble').firstChild.textContent") == "Settings"
                assert browser.js("document.querySelector('.msg.user .file-tag').textContent") == "Search"
            elif stage == "restore":
                browser.wait("document.documentElement.lang === 'zh-CN'")
                assert server.store.settings()["language"] == "zh-CN"
                language(browser, "en")
                # The first catalog is incomplete. A refresh makes the saved model available again.
                expected = {"model": MODEL, "effort": "ultra", "service_tier": "ultrafast", "web_search": True}
                settings = server.store.settings()
                assert {key: settings[key] for key in expected} == expected
                catalog.append(MODEL)
                browser.click("#response-settings")
                browser.click("#refresh-models")
                browser.click("#draft")
                expect_switch(browser, "web-search", True)
                expect_speed_icon(browser, "ultrafast", "Ultrafast")
                for control, label in (("model", MODEL), ("effort", "Ultra"), ("service-tier", "Ultrafast")):
                    assert browser.js(f'document.querySelector("#{control} .choice-value").textContent') == label
                browser.fill("#draft", "Check the saved choices")
                browser.click("#send")
                browser.wait('!!document.querySelector("[data-fork]")')
                assert requests[-1]["model"] == MODEL and requests[-1]["service_tier"] == "ultrafast"
                assert requests[-1]["multi_agent"]["enabled"]
                assert any(tool["type"] == "web_search" for tool in requests[-1]["tools"])
                assert any(tool.get("name") == "run_terminal" for tool in requests[-1]["tools"])
                cid = server.store.list_chats()[0]["id"]
                assert server.store.chat_workspace(cid)["workspace_mode"] == "chat"

                choose(browser, "model", "o3-mini")
                browser.wait('!!document.querySelector("#web-search:disabled")')
                expect_speed_icon(browser, "default", "Standard")
                assert server.store.settings()["web_search"] is True
                browser.fill("#draft", "Check the unsupported model")
                browser.click("#send")
                browser.wait('document.querySelectorAll("[data-fork]").length === 2')
                assert not any(tool["type"] == "web_search" for tool in requests[-1].get("tools", []))
                choose(browser, "model", MODEL)
                expect_switch(browser, "web-search", True)
                browser.click("#tools-toggle")
                browser.click("#auto-review")
                expect_switch(browser, "auto-review", False)
                # A saved-chat switch and a global preference may change before either POST returns.
                browser.js("document.getElementById('terminal-commands').click(); document.getElementById('web-search').click();")
                for control in ("terminal-commands", "web-search"):
                    expect_switch(browser, control, False)
                browser.click('[data-action="new"]')
                browser.click("#tools-toggle")
                for control in ("auto-review", "terminal-commands"):
                    expect_switch(browser, control, False)
                    browser.click("#" + control)
                    expect_switch(browser, control, True)
            else:
                browser.wait("document.documentElement.lang === 'en'")
                expect_switch(browser, "web-search", False)
                assert browser.js('document.querySelector("#model .choice-value").textContent') == MODEL
                assert server.store.settings()["web_search"] is False
                for control in ("auto-review", "terminal-commands"):
                    expect_switch(browser, control, True)
                cid = server.store.list_chats()[0]["id"]
                browser.click(f'[data-chat="{cid}"]')
                for control in ("auto-review", "terminal-commands"):
                    expect_switch(browser, control, False)
                browser.fill("#draft", "Check the saved chat switches")
                browser.click("#send")
                browser.wait('document.querySelectorAll("[data-fork]").length === 3')
                assert not any(tool.get("name") == "run_terminal" for tool in requests[-1].get("tools", []))
        finally:
            browser.view.close()


if __name__ == "__main__":
    if len(sys.argv) == 3:
        application = QApplication(["Relay preference smoke"])
        session(Path(sys.argv[1]), sys.argv[2])
    else:
        with tempfile.TemporaryDirectory() as folder:
            for stage in ("choose", "restore", "verify-off"):
                subprocess.run([sys.executable, __file__, folder, stage], check=True)
        print("Desktop preferences passed: bilingual single-row composer at 320/390/1240 px, menus, chat content and restart persistence.")
