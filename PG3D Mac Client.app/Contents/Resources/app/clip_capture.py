#!/usr/bin/env python3
"""Replay-buffer video for clips, recorded with ScreenCaptureKit.

ffmpeg's screen input (AVCaptureScreenInput, deprecated by Apple) held clips to
about 30 FPS. ScreenCaptureKit delivers the screen at up to 120 FPS, already scaled
on the GPU, and AVAssetWriter encodes it in hardware. The recording is written as
two-second MP4 chunks (seg000.mp4, seg001.mp4, ... wrapping around), each one
starting on a keyframe, like ffmpeg's segments; clips.py joins the newest ones when
a clip is saved.

Usage: clip_capture.py <buffer dir> <fps> <height or 0 for native> <bitrate> <wrap>
Exit codes: 3 = ScreenCaptureKit/AVFoundation missing, 4 = no Screen Recording
permission, 5 = no display. Writes ffmpeg-style "frame=" lines to progress.txt so
the client can show the real capture rate. "q" on stdin stops it cleanly.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path


def log(msg):
    print(f"[clip_capture] {msg}", flush=True)


# Hide from the Dock before PyObjC loads AppKit (prevents the bouncing Python icon).
try:
    import ctypes, ctypes.util as _cu
    _objc_lib = ctypes.cdll.LoadLibrary(_cu.find_library('objc'))
    _objc_lib.objc_getClass.restype = ctypes.c_void_p
    _objc_lib.sel_registerName.restype = ctypes.c_void_p
    _objc_lib.objc_msgSend.restype = ctypes.c_void_p
    _objc_lib.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    ctypes.cdll.LoadLibrary(_cu.find_library('AppKit'))
    _cls = _objc_lib.objc_getClass(b'NSApplication')
    _app = _objc_lib.objc_msgSend(_cls, _objc_lib.sel_registerName(b'sharedApplication'))
    _send_long = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)(('objc_msgSend', _objc_lib))
    _send_long(_app, _objc_lib.sel_registerName(b'setActivationPolicy:'), 2)
    del _objc_lib, _cls, _app, _send_long, _cu
except Exception:
    pass

try:
    import objc
    import AVFoundation as AVF
    import CoreMedia
    import ScreenCaptureKit as SCK
    from Foundation import NSObject, NSRunLoop, NSDate, NSURL
except Exception as e:  # missing framework or macOS older than 13
    log(f"ScreenCaptureKit/AVFoundation unavailable: {e}")
    sys.exit(3)

OUT = Path(sys.argv[1])
FPS = max(15, min(240, int(sys.argv[2])))
HEIGHT = int(sys.argv[3])
BITRATE = sys.argv[4]
WRAP = max(4, int(sys.argv[5]))
SEGMENT_SECONDS = 2.0


def _bits(s):
    s = str(s).strip().upper()
    mult = 1_000_000 if s.endswith("M") else 1_000 if s.endswith("K") else 1
    return int(float(s.rstrip("MK")) * mult)


STATE = {"stream": None, "denied": False, "failed": None, "frames": 0, "stop": False}
LOCK = threading.Lock()
StreamOutput = objc.protocolNamed("SCStreamOutput")
StreamDelegate = objc.protocolNamed("SCStreamDelegate")


class Segmenter:
    """AVAssetWriter per two-second chunk; a chunk becomes visible (renamed from
    .part) only once it's completely written."""

    def __init__(self, width, height):
        self.width, self.height = width, height
        self.writer = self.input = None
        self.start = None
        self.last = None
        self.index = 0
        self.path = None

    def _settings(self):
        return {
            AVF.AVVideoCodecKey: AVF.AVVideoCodecTypeH264,
            AVF.AVVideoWidthKey: self.width,
            AVF.AVVideoHeightKey: self.height,
            AVF.AVVideoCompressionPropertiesKey: {
                AVF.AVVideoAverageBitRateKey: _bits(BITRATE),
                AVF.AVVideoExpectedSourceFrameRateKey: FPS,
                AVF.AVVideoMaxKeyFrameIntervalKey: int(FPS * SEGMENT_SECONDS),
                AVF.AVVideoAllowFrameReorderingKey: False,
                AVF.AVVideoProfileLevelKey: AVF.AVVideoProfileLevelH264HighAutoLevel,
            },
        }

    def _open(self, pts):
        name = f"seg{self.index % WRAP:03d}"
        self.index += 1
        final = OUT / f"{name}.mp4"
        final.unlink(missing_ok=True)  # the oldest chunk makes room
        self.path = OUT / f"{name}.part.mp4"
        self.path.unlink(missing_ok=True)
        url = NSURL.fileURLWithPath_(str(self.path))
        writer, err = AVF.AVAssetWriter.alloc().initWithURL_fileType_error_(url, AVF.AVFileTypeMPEG4, None)
        if writer is None:
            raise RuntimeError(f"AVAssetWriter: {err}")
        inp = AVF.AVAssetWriterInput.alloc().initWithMediaType_outputSettings_(AVF.AVMediaTypeVideo, self._settings())
        inp.setExpectsMediaDataInRealTime_(True)
        if not writer.canAddInput_(inp):
            raise RuntimeError("the encoder doesn't take this video size")
        writer.addInput_(inp)
        if not writer.startWriting():
            raise RuntimeError(f"startWriting: {writer.error()}")
        writer.startSessionAtSourceTime_(pts)
        self.writer, self.input, self.start = writer, inp, pts

    def _close(self):
        writer, inp, path, last = self.writer, self.input, self.path, self.last
        self.writer = self.input = None
        if writer is None:
            return
        inp.markAsFinished()
        if last is not None:
            writer.endSessionAtSourceTime_(last)
        final = path.with_name(path.name.replace(".part", ""))

        def done():
            if writer.status() == AVF.AVAssetWriterStatusCompleted:
                try:
                    os.replace(path, final)
                except OSError:
                    pass
            else:
                log(f"chunk failed: {writer.error()}")
                path.unlink(missing_ok=True)
        writer.finishWritingWithCompletionHandler_(done)

    def add(self, sbuf):
        pts = CoreMedia.CMSampleBufferGetPresentationTimeStamp(sbuf)
        if self.writer is not None:
            age = CoreMedia.CMTimeGetSeconds(CoreMedia.CMTimeSubtract(pts, self.start))
            if age >= SEGMENT_SECONDS:
                self._close()
        if self.writer is None:
            self._open(pts)
        if self.writer.status() == AVF.AVAssetWriterStatusFailed:
            raise RuntimeError(f"encoder failed: {self.writer.error()}")
        if self.input.isReadyForMoreMediaData():
            if self.input.appendSampleBuffer_(sbuf):
                self.last = pts
                return True
        return False

    def finish(self):
        self._close()


SEG = {"s": None}


def _complete(sbuf):
    """Only frames with new content (ScreenCaptureKit also sends idle/blank ones)."""
    try:
        att = CoreMedia.CMSampleBufferGetSampleAttachmentsArray(sbuf, False)
        status = att[0][SCK.SCStreamFrameInfoStatus] if att else None
        return status is None or int(status) == SCK.SCFrameStatusComplete
    except Exception:
        return True


class Output(NSObject, protocols=[StreamOutput, StreamDelegate]):
    def stream_didOutputSampleBuffer_ofType_(self, stream, sbuf, kind):
        if kind != SCK.SCStreamOutputTypeScreen or STATE["stop"] or not _complete(sbuf):
            return
        with LOCK:
            try:
                if SEG["s"] is not None and SEG["s"].add(sbuf):
                    STATE["frames"] += 1
            except Exception as e:
                STATE["failed"] = str(e)

    def stream_didStopWithError_(self, stream, error):
        STATE["failed"] = f"stream stopped: {error}"


OUTPUT = Output.alloc().init()


def start():
    done = threading.Event()

    def got_content(content, error):
        try:
            if error is not None or content is None:
                if error is not None and int(error.code()) == -3801:
                    STATE["denied"] = True
                else:
                    STATE["failed"] = f"no shareable content: {error}"
                return
            displays = content.displays()
            if not displays:
                STATE["failed"] = "no display"
                return
            d = displays[0]
            try:
                from AppKit import NSScreen
                scale = float(NSScreen.mainScreen().backingScaleFactor() or 1.0)
            except Exception:
                scale = 2.0
            nw, nh = int(d.width() * scale), int(d.height() * scale)
            if HEIGHT and HEIGHT < nh:
                h = HEIGHT
                w = int(round(nw * h / nh / 2)) * 2
            else:
                w, h = nw - nw % 2, nh - nh % 2
            flt = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(d, [])
            cfg = SCK.SCStreamConfiguration.alloc().init()
            cfg.setWidth_(w)
            cfg.setHeight_(h)
            cfg.setShowsCursor_(False)
            cfg.setQueueDepth_(8)
            cfg.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, FPS))
            cfg.setPixelFormat_(875704438)  # '420v', what the H.264 encoder takes directly
            SEG["s"] = Segmenter(w, h)
            stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(flt, cfg, OUTPUT)
            ok = stream.addStreamOutput_type_sampleHandlerQueue_error_(OUTPUT, SCK.SCStreamOutputTypeScreen, None, None)
            if isinstance(ok, tuple):
                ok = ok[0]
            if not ok:
                STATE["failed"] = "couldn't add the stream output"
                return

            def started(err):
                if err is not None:
                    if int(err.code()) == -3801:
                        STATE["denied"] = True
                    else:
                        STATE["failed"] = f"capture failed: {err}"
                else:
                    log(f"recording {w}x{h} at up to {FPS} FPS")
            STATE["stream"] = stream
            stream.startCaptureWithCompletionHandler_(started)
        except Exception as e:
            STATE["failed"] = f"start error: {e}"
        finally:
            done.set()

    SCK.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
        True, True, got_content)


def stdin_watch():
    try:
        while True:
            ch = sys.stdin.read(1)
            if not ch or ch == "q":
                break
    except Exception:
        pass
    STATE["stop"] = True


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for old in list(OUT.glob("seg*.mp4")):
        old.unlink(missing_ok=True)
    threading.Thread(target=stdin_watch, daemon=True).start()
    start()
    progress = OUT / "progress.txt"
    last = time.time()
    began = time.time()
    while not STATE["stop"]:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))
        if STATE["denied"]:
            log("Screen Recording permission is missing.")
            sys.exit(4)
        if STATE["failed"]:
            log(STATE["failed"])
            sys.exit(5 if "no display" in STATE["failed"] else 1)
        if STATE["stream"] is None and time.time() - began > 10:
            log("the capture didn't start")
            sys.exit(1)
        if time.time() - last >= 1.0:
            last = time.time()
            try:
                with open(progress, "a") as f:
                    f.write(f"frame={STATE['frames']}\ndup_frames=0\nprogress=continue\n")
            except OSError:
                pass
    stream = STATE["stream"]
    if stream is not None:
        try:
            stream.stopCaptureWithCompletionHandler_(lambda err: None)
        except Exception:
            pass
    with LOCK:
        if SEG["s"] is not None:
            SEG["s"].finish()
    # Let the last chunk finish writing.
    end = time.time() + 3
    while time.time() < end and list(OUT.glob("*.part.mp4")):
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))
    sys.exit(0)


if __name__ == "__main__":
    main()
