#!/usr/bin/env python3
"""Native macOS window hosting the local PG3D Mac Client UI."""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

if os.environ.get("PG3D_EMBEDDED") and os.environ.get("PYTHONPATH"):
    import site
    site.addsitedir(os.environ["PYTHONPATH"])  # the runtime's packages (.pth files included)

from Cocoa import (
    NSAlert, NSAlertFirstButtonReturn, NSModalResponseOK, NSOpenPanel, NSTextField,
    NSApplication, NSApplicationActivationPolicyRegular, NSBackingStoreBuffered,
    NSColor, NSMakeRect, NSObject, NSScreen, NSWindow, NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable, NSWindowStyleMaskTitled,
    NSViewWidthSizable, NSViewHeightSizable,
)
from Foundation import NSTimer, NSURL, NSURLRequest
from WebKit import WKWebView, WKWebViewConfiguration, WKUserContentController, WKWebsiteDataStore
import objc

SUPPORT = Path.home() / "Library/Application Support/PG3D Mac Client"
BG = (0x0c / 255, 0x0d / 255, 0x0f / 255)  # matches --bg in web/index.html


class MessageHandler(NSObject):
    """Receives window.webkit.messageHandlers.pg3d messages from the UI (currently informational)."""

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        pass


def run_alert(message, buttons, field=None):
    """Modal alert; True when the first button was pressed. A plain function on
    purpose: PyObjC turns methods of NSObject subclasses into selectors and
    rejects helpers whose arguments don't match their names."""
    alert = NSAlert.alloc().init()
    alert.setMessageText_(str(message or ""))
    for title in buttons:
        alert.addButtonWithTitle_(title)
    if field is not None:
        alert.setAccessoryView_(field)
        alert.window().setInitialFirstResponder_(field)
    return alert.runModal() == NSAlertFirstButtonReturn


class Shell(NSObject):
    window = objc.ivar()
    web = objc.ivar()
    handler = objc.ivar()
    pending_activate = objc.ivar()
    ticks = objc.ivar()

    def initWithURL_(self, url):
        self = objc.super(Shell, self).init()
        if self is None:
            return None
        screen = NSScreen.mainScreen().visibleFrame()
        w, h = min(1360, screen.size.width - 80), min(900, screen.size.height - 80)
        x = screen.origin.x + (screen.size.width - w) / 2
        y = screen.origin.y + (screen.size.height - h) / 2
        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                 NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, w, h), style, NSBackingStoreBuffered, False)
        self.window.setTitle_("PG3D Mac Client")
        self.window.setMinSize_((980, 660))
        self.window.setBackgroundColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(*BG, 1.0))
        self.window.setDelegate_(self)
        # Remember size/position between launches.
        self.window.setFrameAutosaveName_("PG3DMacClientMain")

        uc = WKUserContentController.alloc().init()
        self.handler = MessageHandler.alloc().init()
        uc.addScriptMessageHandler_name_(self.handler, "pg3d")
        cfg = WKWebViewConfiguration.alloc().init()
        cfg.setUserContentController_(uc)
        cfg.setWebsiteDataStore_(WKWebsiteDataStore.defaultDataStore())

        cv = self.window.contentView()
        self.web = WKWebView.alloc().initWithFrame_configuration_(cv.bounds(), cfg)
        self.web.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        try:
            # Avoid the white flash before the dark UI paints.
            self.web.setValue_forKey_(False, "drawsBackground")
        except Exception:
            pass
        # Without a UI delegate WKWebView silently ignores <input type=file>,
        # alert(), confirm() and prompt().
        self.web.setUIDelegate_(self)
        cv.addSubview_(self.web)
        self.web.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))
        self.pending_activate = False
        return self

    def activate(self):
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    def tick_(self, timer):
        # Python signal handlers only run while the interpreter is active, so a
        # light timer lets SIGUSR1 ("bring window to front") actually take effect.
        if self.pending_activate:
            self.pending_activate = False
            self.activate()
        # A newer copy (after an update) removes this window's pid file when it takes
        # over. If this process is still here then (its quit signal got lost), close it,
        # so the Dock doesn't keep the old copy's icon next to the new one.
        self.ticks = (self.ticks or 0) + 1
        if self.ticks % 7 == 0 and PIDFILE is not None and not PIDFILE.exists():
            os._exit(0)

    # ----- WKUIDelegate
    def webView_runOpenPanelWithParameters_initiatedByFrame_completionHandler_(self, web, params, frame, done):
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(bool(params.allowsDirectories()))
        panel.setAllowsMultipleSelection_(bool(params.allowsMultipleSelection()))
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if panel.runModal() == NSModalResponseOK:
            done(panel.URLs())
        else:
            done(None)

    def webView_runJavaScriptAlertPanelWithMessage_initiatedByFrame_completionHandler_(self, web, message, frame, done):
        run_alert(message, ["OK"])
        done()

    def webView_runJavaScriptConfirmPanelWithMessage_initiatedByFrame_completionHandler_(self, web, message, frame, done):
        done(run_alert(message, ["OK", "Cancel"]))

    def webView_runJavaScriptTextInputPanelWithPrompt_defaultText_initiatedByFrame_completionHandler_(
            self, web, prompt, default, frame, done):
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 260, 24))
        field.setStringValue_(str(default or ""))
        done(field.stringValue() if run_alert(prompt, ["OK", "Cancel"], field) else None)

    def windowWillClose_(self, note):
        NSApplication.sharedApplication().terminate_(None)


PIDFILE = None


def brand_process():
    """The window runs in Python, which macOS would show as "Python" with Python's
    rocket icon. Present it as the client instead: its name and its own icon."""
    name = "PG3D Mac Client"
    try:
        from Foundation import NSBundle, NSProcessInfo
        NSProcessInfo.processInfo().setProcessName_(name)
        info = NSBundle.mainBundle().infoDictionary()
        info["CFBundleName"] = name
        info["CFBundleDisplayName"] = name
    except Exception:
        pass


def set_app_icon(app):
    try:
        from Cocoa import NSImage
        candidates = [Path(__file__).resolve().parent.parent / "AppIcon.icns"]
        bundle = os.environ.get("PG3D_APP_BUNDLE")
        if bundle:
            candidates.insert(0, Path(bundle) / "Contents" / "Resources" / "AppIcon.icns")
        for icon in candidates:
            if icon.is_file():
                image = NSImage.alloc().initWithContentsOfFile_(str(icon))
                if image is not None:
                    app.setApplicationIconImage_(image)
                    break
    except Exception:
        pass


def main():
    if len(sys.argv) < 2:
        raise SystemExit(2)
    global PIDFILE
    pidfile = Path(sys.argv[2]) if len(sys.argv) > 2 else SUPPORT / "shell.pid"
    SUPPORT.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(str(os.getpid()))
    PIDFILE = pidfile
    for k in ("PYTHONHOME", "PYTHONPATH", "PG3D_EMBEDDED"):
        os.environ.pop(k, None)  # set by the app's launcher for this process only
    brand_process()
    try:
        app = NSApplication.sharedApplication()
        set_app_icon(app)
        app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        shell = Shell.alloc().initWithURL_(sys.argv[1])
        shell.activate()

        def on_usr1(*_):
            shell.pending_activate = True
        signal.signal(signal.SIGUSR1, on_usr1)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(0.3, shell, "tick:", None, True)
        app.run()
    finally:
        try:
            pidfile.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # The window couldn't open. Say so (the details are in the log) instead of
        # silently switching to the browser.
        import subprocess
        import traceback
        traceback.print_exc()
        print("window failed to open", flush=True)
        r = subprocess.run(["/usr/bin/osascript", "-e",
                            'button returned of (display dialog "The PG3D Client window could not open. '
                            'Details are in the launcher log (Settings > Diagnostics once it opens)." '
                            'with title "PG3D Client" buttons {"Open in browser", "OK"} default button "OK" with icon caution)'],
                           capture_output=True, text=True)
        if "browser" in (r.stdout or ""):
            subprocess.run(["/usr/bin/open", sys.argv[1]])
        raise SystemExit(1)
