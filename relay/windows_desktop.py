import ctypes
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWebEngineCore import QWebEnginePermission, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from .config import ASSETS, data_dir
from .storage import Store

user32 = ctypes.WinDLL("user32")
user32.IsIconic.argtypes = (wintypes.HWND,)
user32.ShowWindowAsync.argtypes = (wintypes.HWND, ctypes.c_int)
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)


def focus_window(handle):
    if user32.IsIconic(handle):
        user32.ShowWindowAsync(handle, 9)  # SW_RESTORE
    user32.SetForegroundWindow(handle)


def wait_for_exit(text, cancel, still_running):
    app = QApplication.instance() or QApplication(["Relay Chat"])
    dialog = QMessageBox(QMessageBox.Icon.Information, "Relay Chat", text, QMessageBox.StandardButton.Cancel)
    dialog.setWindowIcon(QIcon(str(ASSETS.parent / "assets/relay.ico")))
    dialog.button(QMessageBox.StandardButton.Cancel).setText(cancel)
    released = False

    def check():
        nonlocal released
        if not still_running():
            released = True
            dialog.accept()

    timer = QTimer(dialog)
    timer.timeout.connect(check)
    timer.start(750)
    dialog.exec()
    return released


def allow_microphone(permission, local):
    origin = permission.origin()
    if (permission.permissionType() == QWebEnginePermission.PermissionType.MediaAudioCapture
            and (origin.scheme(), origin.authority()) == (local.scheme(), local.authority())):
        permission.grant()
    else:
        permission.deny()


def show(url, ready, stopped):
    app = QApplication.instance() or QApplication(["Relay Chat"])
    app.setWindowIcon(QIcon(str(ASSETS.parent / "assets/relay.ico")))
    store = Store(data_dir())
    profile = QWebEngineProfile("RelayChat", app)
    profile.setPersistentStoragePath(str(data_dir() / "desktop"))
    profile.setCachePath(str(data_dir() / "desktop-cache"))
    view = QWebEngineView(profile)
    view.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    view.setWindowTitle("Relay Chat")
    view.resize(1240, 840)
    view.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
    local = QUrl(url)

    def external(target):
        if target.scheme() in ("http", "https"):
            QDesktopServices.openUrl(target)

    def navigate(request):
        target = request.url()
        if target.scheme() == "blob":  # Generated Markdown downloads.
            return
        if (target.scheme(), target.authority()) != (local.scheme(), local.authority()):
            request.reject()
            if request.navigationType() == request.NavigationType.LinkClickedNavigation:
                external(target)

    def save(download):
        title = "保存文件" if store.settings()["language"] == "zh-CN" else "Save file"
        path, _ = QFileDialog.getSaveFileName(view, title, str(Path(download.downloadDirectory()) / download.downloadFileName()))
        if path:
            destination = Path(path)
            download.setDownloadDirectory(str(destination.parent))
            download.setDownloadFileName(destination.name)
            download.accept()

    page = view.page()
    page.permissionRequested.connect(lambda permission: allow_microphone(permission, local))
    page.navigationRequested.connect(navigate)
    page.newWindowRequested.connect(lambda request: external(request.requestedUrl()) if request.isUserInitiated() else None)
    profile.downloadRequested.connect(save)
    view.loadFinished.connect(lambda ok: ready(int(view.winId())) if ok else None)
    if stopped is not None:
        timer = QTimer(view)
        timer.timeout.connect(lambda: view.close() if stopped.is_set() else None)
        timer.start(150)
    view.load(local)
    view.show()
    app.exec()
