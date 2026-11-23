"""Finder window and native file dialogs for the Relay workspace."""
import os
import threading
from urllib.parse import urlsplit

import AppKit as A
import WebKit as W
from Foundation import NSRunLoop, NSTimer, NSURL, NSURLRequest
from PyObjCTools import AppHelper

from . import config


def focus_window(pid):
    app = A.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app:
        app.activateWithOptions_(A.NSApplicationActivateIgnoringOtherApps)


def wait_for_exit(text, cancel, still_running):
    app = A.NSApplication.sharedApplication()
    app.setActivationPolicy_(A.NSApplicationActivationPolicyRegular)
    app.activateIgnoringOtherApps_(True)
    alert = A.NSAlert.alloc().init()
    alert.setMessageText_("Relay Chat")
    alert.setInformativeText_(text)
    alert.addButtonWithTitle_(cancel)

    def check(timer):
        if not still_running():
            app.stopModalWithCode_(A.NSModalResponseOK)

    timer = NSTimer.timerWithTimeInterval_repeats_block_(0.75, True, check)
    NSRunLoop.currentRunLoop().addTimer_forMode_(timer, A.NSModalPanelRunLoopMode)
    try:
        return alert.runModal() == A.NSModalResponseOK
    finally:
        timer.invalidate()


# WebKit registers delegate signatures on NSObject. Let PyObjC bind them when
# creating the class so decision and completion blocks retain their argument types.
class WindowDelegate(A.NSObject):
    def userContentController_didReceiveScriptMessage_(self, controller, message):
        self.chinese = message.body() == "zh-CN"
        set_main_menu(self.chinese)

    def windowWillClose_(self, notification):
        A.NSApplication.sharedApplication().stop_(None)

    def webView_didFinishNavigation_(self, view, navigation):
        self.ready(os.getpid())

    def webView_decidePolicyForNavigationAction_decisionHandler_(self, view, action, decide):
        target = action.request().URL()
        if action.shouldPerformDownload() and target.scheme() == "blob":
            decide(W.WKNavigationActionPolicyDownload)
        elif urlsplit(str(target.absoluteString()))[:2] == self.origin:
            decide(W.WKNavigationActionPolicyAllow)
        else:
            decide(W.WKNavigationActionPolicyCancel)
            if target.scheme() in ("http", "https") and action.navigationType() == W.WKNavigationTypeLinkActivated:
                A.NSWorkspace.sharedWorkspace().openURL_(target)

    def webView_navigationAction_didBecomeDownload_(self, view, action, download):
        download.setDelegate_(self)

    def webView_requestMediaCapturePermissionForOrigin_initiatedByFrame_type_decisionHandler_(self, view, origin, frame, kind, decide):
        address = (str(origin.protocol()), f"{origin.host()}:{origin.port()}")
        allow = frame.isMainFrame() and address == self.origin and kind == W.WKMediaCaptureTypeMicrophone
        decide(W.WKPermissionDecisionGrant if allow else W.WKPermissionDecisionDeny)

    def download_decideDestinationUsingResponse_suggestedFilename_completionHandler_(self, download, response, name, finish):
        panel = A.NSSavePanel.savePanel()
        panel.setNameFieldStringValue_(name)
        finish(panel.URL() if panel.runModal() == A.NSModalResponseOK else None)

    def webView_runOpenPanelWithParameters_initiatedByFrame_completionHandler_(self, view, params, frame, finish):
        panel = A.NSOpenPanel.openPanel()
        panel.setAllowsMultipleSelection_(params.allowsMultipleSelection())
        finish(panel.URLs() if panel.runModal() == A.NSModalResponseOK else None)

    def webView_runJavaScriptConfirmPanelWithMessage_initiatedByFrame_completionHandler_(self, view, message, frame, finish):
        alert = A.NSAlert.alloc().init()
        alert.setMessageText_(message)
        alert.addButtonWithTitle_("确定" if self.chinese else "OK")
        alert.addButtonWithTitle_("取消" if self.chinese else "Cancel")
        finish(alert.runModal() == A.NSAlertFirstButtonReturn)


def set_main_menu(chinese):
    def label(english, translated):
        return translated if chinese else english

    menu = A.NSMenu.alloc().init()
    sections = [
        ("Relay Chat", [(label("Quit Relay Chat", "退出 Relay Chat"), "performClose:", "q")]),
        (label("Edit", "编辑"), [
            (label("Undo", "撤销"), "undo:", "z"),
            (label("Cut", "剪切"), "cut:", "x"),
            (label("Copy", "复制"), "copy:", "c"),
            (label("Paste", "粘贴"), "paste:", "v"),
            (label("Select All", "全选"), "selectAll:", "a"),
        ]),
    ]
    for title, items in sections:
        submenu = A.NSMenu.alloc().initWithTitle_(title)
        menu.addItemWithTitle_action_keyEquivalent_(title, None, "").setSubmenu_(submenu)
        for caption, action, key in items:
            submenu.addItemWithTitle_action_keyEquivalent_(caption, action, key)
    A.NSApplication.sharedApplication().setMainMenu_(menu)


def show(url, ready, stopped):
    app = A.NSApplication.sharedApplication()
    app.setActivationPolicy_(A.NSApplicationActivationPolicyRegular)
    icon = config.MAC_APP / "Contents/Resources/relay.icns" if config.MAC_APP else config.ROOT / "assets/relay.icns"
    app.setApplicationIconImage_(A.NSImage.alloc().initWithContentsOfFile_(str(icon)))
    set_main_menu(False)

    frame = A.NSMakeRect(0, 0, 1240, 840)
    style = A.NSWindowStyleMaskTitled | A.NSWindowStyleMaskClosable | A.NSWindowStyleMaskMiniaturizable | A.NSWindowStyleMaskResizable
    window = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(frame, style, A.NSBackingStoreBuffered, False)
    window.setTitle_("Relay Chat")
    window.setReleasedWhenClosed_(False)
    delegate = WindowDelegate.alloc().init()
    delegate.ready = ready
    delegate.chinese = False
    delegate.origin = urlsplit(url)[:2]
    window.setDelegate_(delegate)
    configuration = W.WKWebViewConfiguration.alloc().init()
    configuration.userContentController().addScriptMessageHandler_name_(delegate, "relayLanguage")
    view = W.WKWebView.alloc().initWithFrame_configuration_(frame, configuration)
    view.setUIDelegate_(delegate)
    view.setNavigationDelegate_(delegate)
    window.setContentView_(view)
    window.center()
    window.makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)
    view.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))

    if stopped is not None:
        def close_when_stopped():
            stopped.wait()
            AppHelper.callAfter(window.performClose_, None)
        threading.Thread(target=close_when_stopped, daemon=True).start()
    app.run()
