#!/usr/bin/env python3
"""Measures Pixel Gun 3D's frame rate from the outside, for the overlay.

The in-game measurement needs the FPS unlocker or mouse fix loaded into the game,
which isn't always the case (a Steam start, or a build they don't support). This
helper instead counts how often PG3D's picture actually changes on screen, using
ScreenCaptureKit with a filter that only includes the PG3D app: one tiny frame per
new game frame, nothing is kept. Needs Screen Recording permission (the same one
clips use). Writes {"fps", "at"} to state/game_fps.json once a second.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

SUPPORT = Path.home() / "Library/Application Support/PG3D Mac Client"
OUT = SUPPORT / "state/game_fps.json"
PIDFILE = SUPPORT / "game_fps.pid"


def log(msg):
    print(f"[game_fps] {msg}", flush=True)


try:
    import objc
    import CoreMedia
    import ScreenCaptureKit as SCK
    from Foundation import NSObject, NSRunLoop, NSDate, NSWorkspace
except Exception as e:  # missing framework or macOS older than 13
    log(f"ScreenCaptureKit unavailable: {e}")
    sys.exit(3)


def pg3d_running():
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if "pixel gun" in str(app.localizedName() or "").lower():
            return True
    return False


COUNT = {"n": 0}
LOCK = threading.Lock()
StreamOutput = objc.protocolNamed("SCStreamOutput")
StreamDelegate = objc.protocolNamed("SCStreamDelegate")


def _complete(sbuf):
    """Only frames with new content count (idle repeats are skipped)."""
    try:
        att = CoreMedia.CMSampleBufferGetSampleAttachmentsArray(sbuf, False)
        status = att[0][SCK.SCStreamFrameInfoStatus] if att else None
        return status is None or int(status) == SCK.SCFrameStatusComplete
    except Exception:
        return True


class Output(NSObject, protocols=[StreamOutput, StreamDelegate]):
    def stream_didOutputSampleBuffer_ofType_(self, stream, sbuf, kind):
        if kind == SCK.SCStreamOutputTypeScreen and _complete(sbuf):
            with LOCK:
                COUNT["n"] += 1

    def stream_didStopWithError_(self, stream, error):
        log(f"stream stopped: {error}")
        STATE["stream"] = None


STATE = {"stream": None, "pending": False, "denied": False, "output": Output.alloc().init()}


def _start_stream():
    STATE["pending"] = True

    def got_content(content, error):
        try:
            if error is not None or content is None:
                if error is not None and int(error.code()) == -3801:
                    log("Screen Recording permission is missing; FPS comes only from the game itself.")
                    STATE["denied"] = True
                else:
                    log(f"no shareable content: {error}")
                return
            apps = [a for a in content.applications() if "pixel gun" in str(a.applicationName() or "").lower()]
            displays = content.displays()
            if not apps or not displays:
                return
            flt = SCK.SCContentFilter.alloc().initWithDisplay_includingApplications_exceptingWindows_(displays[0], apps, [])
            cfg = SCK.SCStreamConfiguration.alloc().init()
            cfg.setWidth_(64)
            cfg.setHeight_(40)
            cfg.setShowsCursor_(False)
            cfg.setQueueDepth_(5)
            cfg.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, 240))  # never the limit
            stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(flt, cfg, STATE["output"])
            stream.addStreamOutput_type_sampleHandlerQueue_error_(STATE["output"], SCK.SCStreamOutputTypeScreen, None, None)

            def started(err):
                if err is not None:
                    log(f"capture failed: {err}")
                    STATE["stream"] = None
                else:
                    log("measuring PG3D's frame rate")

            STATE["stream"] = stream
            stream.startCaptureWithCompletionHandler_(started)
        except Exception as e:
            log(f"start error: {e}")
        finally:
            STATE["pending"] = False

    SCK.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
        True, True, got_content)


def _stop_stream():
    stream, STATE["stream"] = STATE["stream"], None
    if stream is not None:
        try:
            stream.stopCaptureWithCompletionHandler_(lambda err: None)
        except Exception:
            pass


def _write(fps):
    try:
        tmp = OUT.with_suffix(".tmp")
        tmp.write_text(json.dumps({"fps": fps, "at": time.time()}))
        os.replace(tmp, OUT)
    except Exception:
        pass


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    PIDFILE.write_text(str(os.getpid()))
    parent = os.getppid()
    last_check = last_sample = time.time()
    while True:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.2))
        now = time.time()
        if now - last_sample >= 1.0:
            with LOCK:
                n, COUNT["n"] = COUNT["n"], 0
            if STATE["stream"] is not None:
                _write(round(n / (now - last_sample)))
            last_sample = now
        if now - last_check < 2:
            continue
        last_check = now
        if os.getppid() != parent:  # the overlay went away
            break
        if STATE["denied"]:
            _stop_stream()
            PIDFILE.unlink(missing_ok=True)
            sys.exit(4)
        running = pg3d_running()
        if running and STATE["stream"] is None and not STATE["pending"]:
            _start_stream()
        elif not running and STATE["stream"] is not None:
            _stop_stream()
    _stop_stream()
    PIDFILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
