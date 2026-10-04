#!/usr/bin/env python3
"""Records Pixel Gun 3D's own sound for clips.

Runs as a small helper process next to the replay buffer. It uses Apple's
ScreenCaptureKit (macOS 13+) with a filter that only includes the PG3D app, so
Spotify, Discord, notifications and everything else stay out of the clip.

Audio is written as one-second raw chunks (48 kHz stereo float32, interleaved)
named by the wall-clock time of their first sample. clips.py picks the chunks
that cover a saved clip and muxes them in. Old chunks are deleted as it goes.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from array import array
from pathlib import Path

SUPPORT = Path.home() / "Library/Application Support/PG3D Mac Client"
AUDIO_DIR = SUPPORT / "replay_audio"
PIDFILE = SUPPORT / "game_audio.pid"
RATE = 48000
CHANNELS = 2
KEEP_SECONDS = 200
CHUNK_SECONDS = 1.0


def log(msg):
    print(f"[game_audio] {msg}", flush=True)


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
        name = str(app.localizedName() or "")
        if "pixel gun" in name.lower():
            return True
    return False


class Writer:
    def __init__(self):
        self.buf = bytearray()
        self.start = None
        self.lock = threading.Lock()

    def add(self, pcm: bytes, frames: int):
        now = time.time()
        with self.lock:
            if self.start is None:
                self.start = now - frames / RATE
            self.buf += pcm
            full = len(self.buf) >= int(RATE * CHUNK_SECONDS) * CHANNELS * 4
        if full:
            self.flush()

    def flush(self):
        with self.lock:
            if not self.buf or self.start is None:
                return
            data, start = bytes(self.buf), self.start
            self.buf = bytearray()
            self.start = None
        name = AUDIO_DIR / f"a{int(start * 1000)}.f32"
        tmp = name.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, name)
        cutoff = time.time() - KEEP_SECONDS
        for p in AUDIO_DIR.glob("a*.f32"):
            try:
                if int(p.stem[1:]) / 1000 < cutoff:
                    p.unlink()
            except (ValueError, OSError):
                pass


WRITER = Writer()


def _block_bytes(sbuf):
    bb = CoreMedia.CMSampleBufferGetDataBuffer(sbuf)
    if bb is None:
        return b""
    n = CoreMedia.CMBlockBufferGetDataLength(bb)
    try:
        res = CoreMedia.CMBlockBufferCopyDataBytes(bb, 0, n, None)
        data = res[1] if isinstance(res, tuple) else res
    except TypeError:
        out = bytearray(n)
        CoreMedia.CMBlockBufferCopyDataBytes(bb, 0, n, out)
        data = out
    return bytes(data or b"")


def _interleave(data: bytes, frames: int) -> bytes:
    # ScreenCaptureKit hands out non-interleaved (planar) float32: all left
    # samples, then all right samples. Interleave them for ffmpeg's f32le.
    if frames <= 0 or len(data) != frames * CHANNELS * 4:
        return data
    a = array("f")
    a.frombytes(data)
    out = array("f", bytes(len(data)))
    out[0::2] = a[:frames]
    out[1::2] = a[frames:]
    return out.tobytes()


StreamOutput = objc.protocolNamed("SCStreamOutput")
StreamDelegate = objc.protocolNamed("SCStreamDelegate")


class Output(NSObject, protocols=[StreamOutput, StreamDelegate]):
    def stream_didOutputSampleBuffer_ofType_(self, stream, sbuf, kind):
        if kind != SCK.SCStreamOutputTypeAudio:
            return
        try:
            frames = int(CoreMedia.CMSampleBufferGetNumSamples(sbuf))
            data = _block_bytes(sbuf)
            if data:
                WRITER.add(_interleave(data, frames), frames)
        except Exception as e:
            log(f"sample error: {e}")

    def stream_didStopWithError_(self, stream, error):
        log(f"stream stopped: {error}")
        STATE["stream"] = None


STATE = {"stream": None, "pending": False, "output": Output.alloc().init()}


def _start_stream():
    STATE["pending"] = True

    def got_content(content, error):
        try:
            if error is not None or content is None:
                log(f"no shareable content: {error}")
                return
            apps = [a for a in content.applications() if "pixel gun" in str(a.applicationName() or "").lower()]
            displays = content.displays()
            if not apps or not displays:
                return
            flt = SCK.SCContentFilter.alloc().initWithDisplay_includingApplications_exceptingWindows_(displays[0], apps, [])
            cfg = SCK.SCStreamConfiguration.alloc().init()
            cfg.setCapturesAudio_(True)
            cfg.setSampleRate_(RATE)
            cfg.setChannelCount_(CHANNELS)
            cfg.setExcludesCurrentProcessAudio_(True)
            # Video is required by the API; keep it tiny and slow.
            cfg.setWidth_(2)
            cfg.setHeight_(2)
            cfg.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, 1))
            stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(flt, cfg, STATE["output"])
            for kind in (SCK.SCStreamOutputTypeScreen, SCK.SCStreamOutputTypeAudio):
                stream.addStreamOutput_type_sampleHandlerQueue_error_(STATE["output"], kind, None, None)

            def started(err):
                if err is not None:
                    log(f"capture failed: {err}")
                    STATE["stream"] = None
                else:
                    log("capturing PG3D audio")

            STATE["stream"] = stream
            stream.startCaptureWithCompletionHandler_(started)
        except Exception as e:
            log(f"start error: {e}")
        finally:
            STATE["pending"] = False

    SCK.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
        True, False, got_content)


def _stop_stream():
    stream, STATE["stream"] = STATE["stream"], None
    if stream is not None:
        try:
            stream.stopCaptureWithCompletionHandler_(lambda err: None)
        except Exception:
            pass
    WRITER.flush()


def main():
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    for p in AUDIO_DIR.glob("*"):
        p.unlink(missing_ok=True)
    PIDFILE.write_text(str(os.getpid()))
    parent = os.getppid()
    last_check = 0.0
    while True:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.25))
        now = time.time()
        if now - last_check < 2:
            continue
        last_check = now
        if os.getppid() != parent:  # the client went away
            break
        running = pg3d_running()
        if running and STATE["stream"] is None and not STATE["pending"]:
            _start_stream()
        elif not running and STATE["stream"] is not None:
            _stop_stream()
        if WRITER.start is not None and now - WRITER.start > CHUNK_SECONDS * 2:
            WRITER.flush()
    _stop_stream()
    PIDFILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
