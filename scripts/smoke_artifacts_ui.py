"""Open a report saved by an older Relay build and download it through the desktop UI."""
import tempfile
import time
from pathlib import Path

from smoke_workspace_ui import Browser, QApplication, QTest, QUrl, RelayServer, models, serving
from test_artifacts import legacy_report, workbook


def smoke(root):
    app = RelayServer(0, root / "data", key_source=lambda: ("fixture", "test"), codex_home=root,
                      model_source=lambda key: [models.capabilities("gpt-5.4-mini")])
    report = workbook()
    cid, _ = legacy_report(app.store, report)
    saved = root / "saved.xlsx"
    browser = Browser(None)

    def accept_download(request):
        request.setDownloadDirectory(str(root))
        request.setDownloadFileName(saved.name)
        request.accept()

    browser.page.profile().downloadRequested.connect(accept_download)
    with serving(app) as url:
        try:
            browser.view.load(QUrl(url))
            browser.click(f'[data-chat="{cid}"]')
            browser.click(".answer [data-artifact]")
            browser.wait('document.querySelector(".artifact-preview td:last-child")?.textContent === "12"')
            browser.click("[data-save-output]")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if saved.is_file() and saved.stat().st_size == len(report):
                    break
                QTest.qWait(25)
            assert saved.read_bytes() == report
            browser.click("#close-modal")
            browser.click('[data-action="new"]')
            browser.click(f'[data-chat="{cid}"]')
            browser.wait('!!document.querySelector(".msg .artifact-card")')
            browser.click("header [data-chat-files]")
            browser.wait('document.querySelector("#modal-body .artifact-card")?.textContent.includes("report.xlsx")')
            browser.click("#close-modal")
            browser.click("#more")
            browser.click("#delete")
            browser.wait("document.querySelector('#delete-folder')?.checked === true")
            browser.click("#cancel-delete")
            assert app.store.chat_folder(cid).is_dir()
            browser.click("#more")
            browser.click("#delete")
            browser.click("#confirm-delete")
            browser.wait(f"!document.querySelector('[data-chat=\"{cid}\"]') && !document.querySelector('#dialog').open")
            assert not app.store.chat_folder(cid).exists()
            assert saved.read_bytes() == report
        finally:
            browser.view.close()
    print("Desktop files passed: legacy workbook preview, download, reopened files and chat folder cleanup.")


if __name__ == "__main__":
    application = QApplication(["Relay file UI smoke"])
    with tempfile.TemporaryDirectory() as temporary:
        smoke(Path(temporary))
