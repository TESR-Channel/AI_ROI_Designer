#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TESR Edge AI Vision Runner
==========================
Thai Embedded Systems and Robotics — Build, Train and Deploy AI to Edge Devices
Runs the ROI configuration exported from TESR ROI Studio (index.html).

One config file -> many cameras -> many ROIs -> one task per ROI.
Runs on Windows / macOS / Linux / Raspberry Pi / NVIDIA Jetson with the same code.

Minimum requirement : Python 3.8+, opencv-python, numpy
Optional            : ultralytics (YOLO), pytesseract or easyocr (OCR),
                      pyzbar (1D barcode), paho-mqtt (MQTT)

Usage
-----
    python tesr_vision_runner.py --config config.json --test
    python tesr_vision_runner.py --config config.json --camera usb --source 0 --test
    python tesr_vision_runner.py --config config.json --camera csi --headless
    python tesr_vision_runner.py --config config.json --camera ip --source rtsp://...
    python tesr_vision_runner.py --config config.json --image sample.jpg --once
    python tesr_vision_runner.py --config config.json --source clip.mp4 --test
    python tesr_vision_runner.py --config config.json --camera-id cam2 --headless
    python tesr_vision_runner.py --config config.json --calibrate roi_1
    python tesr_vision_runner.py --config config.json --headless --reset-counters
    python tesr_vision_runner.py --config config.json --selftest

Author: TESR Co., Ltd. - Thai Embedded Systems and Robotics
License: MIT
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import signal
import sys
import time
from datetime import datetime

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "[FATAL] opencv-python / numpy not found.\n"
        "        pip install opencv-python numpy\n"
    )
    raise

SCHEMA = "tesr.roi.v1"
# Bumped whenever the Studio or this runner changes in a way that could make
# an old config or an old runner behave differently. The Studio stamps the
# same number into every config it exports, and load_config() below says so
# out loud when the two do not match - guessing whether your files are in
# step is not a thing anybody should have to do.
STUDIO_VERSION = "1.7.2"

# The line and zone engine lives in its own module so it can be tested on its
# own; the runner works with or without it present.
try:
    import tesr_line_engine as LINE
except ImportError:  # pragma: no cover
    LINE = None

# ---------------------------------------------------------------------------
#  YOUR CODE GOES HERE
#
#  Every value the runner reads passes through on_result().
#  Every event (a shift rollover, for example) passes through on_event().
#  Add what you need and leave the rest of the file alone.
#
#  rec = {"time": ..., "project": ..., "camera": ..., "roi": ...,
#         "key": ..., "value": ..., "conf": ...}
#  `changed` is True only when the value is different from the last one.
# ---------------------------------------------------------------------------


def on_result(rec, changed):
    """Called once per published reading. Edit freely."""
    pass


def on_event(rec):
    """Called for events such as a counter period ending. Edit freely."""
    pass


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

RUNNING = True


def _stop(signum, frame):
    """Ctrl+C / SIGTERM -> stop the main loop, close resources properly."""
    global RUNNING
    RUNNING = False


def log(msg, level="INFO"):
    print("[%s] %s" % (level, msg), flush=True)


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _ver(s):
    """'1.3.2' -> (1, 3, 2), so versions sort as numbers and not as text.

    Compared as strings, '1.10.0' sorts BEFORE '1.9.0', which would make the
    check below start lying the first time a minor version reaches ten.
    """
    parts = []
    for piece in str(s or "0").split("."):
        digits = "".join(c for c in piece if c.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_jetson():
    return (os.path.exists("/etc/nv_tegra_release")
            or "tegra" in platform.release().lower())


CAMERA_KINDS = ("usb", "csi", "ip", "file")


def camera_kind(value):
    """--camera says what KIND of camera, the same way predict.py does.

    Chapters 5 and 6 teach `--camera usb --source 1`, so the runner uses the
    same words rather than a second convention for the same idea. Choosing a
    camera out of the config by name is the rarer job and moved to --camera-id.
    """
    v = (value or "").strip().lower()
    if v in CAMERA_KINDS:
        return v
    raise argparse.ArgumentTypeError(
        "%r is not a camera type. Use one of: %s.\n"
        "  To pick a camera out of the config BY NAME, use --camera-id %s instead."
        % (value, ", ".join(CAMERA_KINDS), value))


def names_of(value):
    """Whatever a region published, as a plain list of class names.

    A detect region set to publish class names gives a LIST: [] for an empty
    region, one name for a filled one, occasionally two when the crop catches
    the edge of its neighbour. A classify region gives a single string. Both
    are handled, so changing a region's task later does not quietly change the
    meaning of every verdict built on it.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        seq = value
    elif isinstance(value, dict):
        return []                      # a counter's {'in':.., 'out':..} is not a name
    else:
        seq = [value]

    out = []
    for v in seq:
        # params.output = "items" publishes a list of DICTS, one per detection,
        # with the name under "class". Reading only the strings here would make
        # every verdict on an items region come back "empty" while the thing was
        # sitting there detected, which is the exact failure the verdict exists
        # to prevent. "name" is the browser preview's spelling of the same key.
        if isinstance(v, dict):
            v = v.get("class") or v.get("name")
        if isinstance(v, str) and v not in ("", "empty"):
            out.append(v)
    return out


def verdict_for(roi, value):
    """ok / wrong / empty for one reading, or None when there is nothing to judge.

    This never changes what the region detected or published. It is only an
    opinion about what SHOULD have been there, taken from params.expect, which
    the Studio sets from a dropdown of the model's own class list.

    Deliberately NOT a filter. A region that discarded the wrong part would
    report an empty slot instead of a fault, and "empty" is the one answer an
    inspection station must never give while the wrong thing sits in front of
    the camera.
    """
    expect = (roi.get("params") or {}).get("expect")
    if not expect:
        return None
    # a count has no names in it, so it cannot answer "is this the right part"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return None
    found = names_of(value)
    if not found:
        return "empty"
    return "ok" if expect in found else "wrong"


_GUI = None


def gui_available():
    """Can this OpenCV build open a window at all?

    `pip install opencv-python-headless` - the sensible choice on a Pi, a
    Jetson running as a service, or any machine with no display - ships HighGUI
    as stubs that raise. Asking once, instead of finding out from an exception
    while shutting down, is the difference between "running headless" and
    "crashed on exit".

    There is a second way to have no GUI, and it is the common one: a full
    OpenCV build on a machine with no display, which is what you get the moment
    you SSH into a Raspberry Pi. destroyAllWindows() succeeds there because it
    touches nothing, and then imshow() calls abort() inside Qt - a process
    death, not a Python exception, so no try/except can catch it and the
    readings stop with a wall of Qt text and no explanation. Asking the
    environment first is the only thing that works.
    """
    global _GUI
    if _GUI is None:
        if (platform.system() == "Linux"
                and not os.environ.get("DISPLAY")
                and not os.environ.get("WAYLAND_DISPLAY")):
            _GUI = False
            return _GUI
        try:
            cv2.destroyAllWindows()
            _GUI = True
        except Exception:
            _GUI = False
    return _GUI


def close_windows():
    if gui_available():
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass


def ensure_dir(path):
    if path:
        d = os.path.dirname(os.path.abspath(path))
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

DEFAULT_RUNTIME = {
    "loop_fps": 10,
    "show_window": True,
    "print_console": True,
    "jsonl_log": "",
    "csv_log": "",
    "save_on_change": False,
    "save_dir": "captures",
    "mqtt": {"enabled": False, "host": "localhost", "port": 1883,
             "topic_prefix": "tesr/vision", "username": "", "password": ""},
    "webhook": {"enabled": False, "url": ""},
    # When the directional counters go back to zero. "shift" uses the times in
    # "shifts"; see PeriodClock in tesr_line_engine.py for the other modes.
    # state_file keeps the counts across a reboot, which matters on a Pi that
    # loses power more often than it finishes a shift.
    "counter_reset": {"mode": "none", "at": "00:00", "shifts": [],
                      "minutes": 60, "state_file": ""},
}


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if cfg.get("schema") != SCHEMA:
        log("config schema is '%s', expected '%s' - trying anyway"
            % (cfg.get("schema"), SCHEMA), "WARN")
    cfg_ver = cfg.get("studio_version")
    if cfg_ver and _ver(cfg_ver) > _ver(STUDIO_VERSION):
        # Only this direction is dangerous. A config written by a NEWER Studio
        # can carry fields this runner has never heard of, and it will ignore
        # them without a word - which is how a region quietly loses the thing
        # you set on it. The other direction is safe and is the normal way this
        # course is used: the chapters ship hand-written configs and ask you to
        # run them with a runner you exported later. Warning on that would put
        # a warning on every correct run, which teaches people to ignore them.
        log("this config was exported by Studio v%s, which is NEWER than this "
            "runner (v%s). Anything added since v%s will be ignored without "
            "being mentioned again. Export the runner from the same Studio - "
            "hard-refresh the page first."
            % (cfg_ver, STUDIO_VERSION, STUDIO_VERSION), "WARN")
    elif not cfg_ver:
        log("this config predates version stamping - re-export it when you can",
            "WARN")
    rt = dict(DEFAULT_RUNTIME)
    rt.update(cfg.get("runtime") or {})
    for key in ("mqtt", "webhook", "counter_reset"):
        merged = dict(DEFAULT_RUNTIME[key])
        merged.update(rt.get(key) or {})
        rt[key] = merged
    cfg["runtime"] = rt
    cfg.setdefault("cameras", [])

    # An expected class on a region that only publishes a NUMBER can never be
    # judged - a count carries no names. Silence here would look like a station
    # that simply never finds a fault, so say it once at startup instead.
    for cam in cfg["cameras"]:
        for roi in cam.get("rois") or []:
            p = roi.get("params") or {}
            if p.get("expect") and roi.get("task") == "yolo_detect" \
                    and p.get("output", "count") == "count":
                log("region '%s' expects '%s' but publishes a count, which has "
                    "no class names in it - no verdict can be reached. Set "
                    "Value to output = Class names for this region."
                    % (roi.get("name"), p["expect"]), "WARN")
    return cfg


def pick_camera(cfg, camera_id=None):
    cams = cfg.get("cameras") or []
    if not cams:
        raise SystemExit("[FATAL] no camera in config")
    if camera_id:
        for c in cams:
            if c.get("id") == camera_id or c.get("name") == camera_id:
                return c
        raise SystemExit("[FATAL] camera '%s' not found in config" % camera_id)
    return cams[0]


# --------------------------------------------------------------------------
# capture source (index / file / folder / rtsp / http)
# --------------------------------------------------------------------------

IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


class FrameSource:
    """Uniform reader for webcam index, ribbon camera, video, image or stream.

    `kind` is what --camera says, using the same words as predict.py in
    chapters 5 and 6: usb, csi, ip or file. Left as None the source is read for
    what it looks like, which is what every config written before v1.3.0
    relies on, so nothing that worked before stops working.
    """

    def __init__(self, source, width=0, height=0, kind=None):
        self.raw = source
        self.kind = "camera"
        self.cap = None
        self.still = None
        self.picam = None

        src = source
        if isinstance(src, str) and src.strip().isdigit():
            src = int(src.strip())

        if kind == "csi":
            self._open_csi(src if isinstance(src, int) else 0,
                           int(width or 1280), int(height or 720))
            return

        # Say which flag is wrong rather than failing later with an open error
        if kind == "ip" and isinstance(src, int):
            raise SystemExit(
                "[FATAL] --camera ip needs a stream URL in --source, not the index %r\n"
                "        e.g. --camera ip --source rtsp://user:pass@192.168.1.50:554/stream"
                % src)
        if kind == "usb" and not isinstance(src, int):
            raise SystemExit(
                "[FATAL] --camera usb needs an index in --source - 0, 1, 2 ... - not %r\n"
                "        an image goes with --image, a video or a stream with --source"
                % src)

        if isinstance(src, str) and os.path.isfile(src) and src.lower().endswith(IMG_EXT):
            self.kind = "image"
            self.still = cv2.imread(src)
            if self.still is None:
                raise SystemExit("[FATAL] cannot read image: %s" % src)
            return

        api = 0
        if isinstance(src, int):
            system = platform.system()
            if system == "Windows":
                api = cv2.CAP_DSHOW
            elif system == "Linux":
                api = cv2.CAP_V4L2

        self.cap = cv2.VideoCapture(src, api) if api else cv2.VideoCapture(src)
        if not self.cap.isOpened():
            # Now that --camera says which kind it is, say the one thing that
            # helps rather than a list the reader has to pick their case out of
            if kind == "usb" or (kind is None and isinstance(src, int)):
                raise SystemExit(
                    "[FATAL] could not open USB camera %r.\n"
                    "        try another index:  --camera usb --source 0   (then 1, then 2)\n"
                    "        list them on Linux:  v4l2-ctl --list-devices\n"
                    "        close anything else holding it - a browser tab, Zoom, Teams\n"
                    "        on macOS allow Camera under System Settings > Privacy\n"
                    "        ribbon camera? that is  --camera csi" % src)
            if kind == "ip":
                raise SystemExit(
                    "[FATAL] could not open the stream: %r\n"
                    "        check the URL, the user and password, and that this\n"
                    "        machine can reach the camera:  ping <its address>\n"
                    "        try the same URL in VLC first - if VLC cannot play it,\n"
                    "        the problem is the camera or the network, not this runner"
                    % source)
            raise SystemExit(
                "[FATAL] cannot open source: %r\n"
                "        file   -> check the path, and that the file is not still copying\n"
                "        webcam -> --camera usb --source 0  (then 1, 2)\n"
                "        stream -> --camera ip --source rtsp://..." % source)
        if width:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(width))
        if height:
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(height))
        if isinstance(src, str) and os.path.isfile(src):
            self.kind = "video"

    def _open_csi(self, index, width, height):
        """A ribbon camera is not a webcam, and is not the same thing twice.

        On a Raspberry Pi it is a libcamera device reached through Picamera2,
        and cv2.VideoCapture cannot open it at all - which is why a CSI camera
        used to look simply broken here. On a Jetson it is nvarguscamerasrc
        reached through a GStreamer pipeline, and pip's opencv-python has no
        GStreamer compiled in, so it needs the system OpenCV that JetPack
        installed. One flag, two entirely different paths underneath.
        """
        if not is_jetson():
            try:
                from picamera2 import Picamera2
            except ImportError:
                raise SystemExit(
                    "[FATAL] a CSI ribbon camera needs Picamera2:\n"
                    "          sudo apt install -y python3-picamera2\n"
                    "        and a venv built with --system-site-packages, "
                    "which install_pi.sh does for you.\n"
                    "        A USB webcam needs none of this: --camera usb --source 0")
            self.picam = Picamera2(index)
            self.picam.configure(self.picam.create_preview_configuration(
                main={"format": "RGB888", "size": (width, height)}))  # RGB888 is BGR to OpenCV
            self.picam.start()
            time.sleep(0.5)
            return

        pipe = ("nvarguscamerasrc sensor-id=%d ! "
                "video/x-raw(memory:NVMM),width=1280,height=720,framerate=30/1 ! "
                "nvvidconv flip-method=0 ! "
                "video/x-raw,width=%d,height=%d,format=BGRx ! "
                "videoconvert ! video/x-raw,format=BGR ! "
                "appsink drop=1 max-buffers=1" % (index, width, height))
        self.cap = cv2.VideoCapture(pipe, cv2.CAP_GSTREAMER)
        if not self.cap.isOpened():
            raise SystemExit(
                "[FATAL] could not open the Jetson CSI camera (sensor-id=%d).\n"
                "        check the ribbon, then:  gst-launch-1.0 nvarguscamerasrc ! fakesink\n"
                "        and check OpenCV has GStreamer:\n"
                "          python3 -c \"import cv2;print(cv2.getBuildInformation())\" | grep -i gstreamer\n"
                "        pip's opencv-python has none - use JetPack's system OpenCV."
                % index)

    def read(self):
        if self.kind == "image":
            return True, self.still.copy()
        if self.picam is not None:
            return True, self.picam.capture_array()
        ok, frame = self.cap.read()
        if not ok and self.kind == "video":
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # loop the test video
            ok, frame = self.cap.read()
        return ok, frame

    def release(self):
        if self.picam is not None:
            self.picam.stop()
        if self.cap is not None:
            self.cap.release()


# --------------------------------------------------------------------------
# geometry: normalised rotated rect -> upright crop
# --------------------------------------------------------------------------

def roi_corners(rect, frame_w, frame_h):
    """Return the 4 corners (px) of a normalised, optionally rotated rect."""
    cx = (rect["x"] + rect["w"] / 2.0) * frame_w
    cy = (rect["y"] + rect["h"] / 2.0) * frame_h
    w = rect["w"] * frame_w
    h = rect["h"] * frame_h
    a = np.deg2rad(float(rect.get("angle", 0)))
    ca, sa = np.cos(a), np.sin(a)
    pts = []
    for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        pts.append([cx + dx * ca - dy * sa, cy + dx * sa + dy * ca])
    return np.array(pts, dtype=np.float32)


def crop_roi(frame, rect, upscale_to=0):
    """Perspective-warp the ROI into an upright image (works with rotation)."""
    h, w = frame.shape[:2]
    src = roi_corners(rect, w, h)
    tw = max(8, int(round(rect["w"] * w)))
    th = max(8, int(round(rect["h"] * h)))
    if upscale_to and th < upscale_to:
        scale = float(upscale_to) / th
        tw, th = int(tw * scale), int(th * scale)
    dst = np.array([[0, 0], [tw - 1, 0], [tw - 1, th - 1], [0, th - 1]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(frame, M, (tw, th))


# --------------------------------------------------------------------------
# task 1 : seven segment / digit reader  (pure OpenCV, no model needed)
# --------------------------------------------------------------------------

SEG_TABLE = {
    (1, 1, 1, 0, 1, 1, 1): "0",
    (0, 0, 1, 0, 0, 1, 0): "1",
    (1, 0, 1, 1, 1, 0, 1): "2",
    (1, 0, 1, 1, 0, 1, 1): "3",
    (0, 1, 1, 1, 0, 1, 0): "4",
    (1, 1, 0, 1, 0, 1, 1): "5",
    (1, 1, 0, 1, 1, 1, 1): "6",
    (1, 0, 1, 0, 0, 1, 0): "7",
    (1, 1, 1, 1, 1, 1, 1): "8",
    (1, 1, 1, 1, 0, 1, 1): "9",
    (0, 0, 0, 1, 0, 0, 0): "-",
    (0, 0, 0, 0, 0, 0, 0): "",
}

# segment boxes as (x0, y0, x1, y1) ratio inside one digit cell
SEG_BOXES = [
    (0.18, 0.00, 0.82, 0.16),   # 0 top
    (0.00, 0.10, 0.24, 0.46),   # 1 top-left
    (0.76, 0.10, 1.00, 0.46),   # 2 top-right
    (0.18, 0.42, 0.82, 0.58),   # 3 middle
    (0.00, 0.54, 0.24, 0.90),   # 4 bottom-left
    (0.76, 0.54, 1.00, 0.90),   # 5 bottom-right
    (0.18, 0.84, 0.82, 1.00),   # 6 bottom
]


def binarize(gray, p):
    blur = int(p.get("blur", 3))
    if blur >= 3 and blur % 2 == 1:
        gray = cv2.GaussianBlur(gray, (blur, blur), 0)
    mode = p.get("threshold_mode", "otsu")
    if mode == "fixed":
        _, bw = cv2.threshold(gray, int(p.get("threshold", 128)), 255, cv2.THRESH_BINARY)
    elif mode == "adaptive":
        blk = int(p.get("block_size", 31))
        blk = blk if blk % 2 == 1 else blk + 1
        bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, blk, int(p.get("c", 5)))
    else:
        _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if p.get("invert", True):
        bw = 255 - bw
    k = int(p.get("morph", 0))
    if k > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
        bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel)
    return bw


SEG_IS_HORIZONTAL = (True, False, False, True, False, False, True)


def _read_cell(bw, box, min_fill):
    """Decide which of the 7 segments are lit inside one digit box.

    A segment is measured by the strongest single line inside its area
    (best row for a horizontal segment, best column for a vertical one),
    so the result does not depend on how thick the segment is drawn.
    """
    x0, y0, x1, y1 = box
    cell = bw[y0:y1, x0:x1]
    ch, cw = cell.shape[:2]
    if ch < 8 or cw < 2:
        return "", 0.0
    ink = float(np.count_nonzero(cell)) / (ch * cw)
    # a narrow but well filled box can only be a "1"
    if cw < 0.34 * ch and ink > 0.20:
        return "1", min(1.0, ink * 2)

    state, margins = [], []
    for i, (sx0, sy0, sx1, sy1) in enumerate(SEG_BOXES):
        ax0, ay0 = int(sx0 * cw), int(sy0 * ch)
        ax1, ay1 = max(ax0 + 1, int(sx1 * cw)), max(ay0 + 1, int(sy1 * ch))
        patch = (cell[ay0:ay1, ax0:ax1] > 0)
        if patch.size == 0:
            state.append(0)
            margins.append(0.0)
            continue
        axis = 1 if SEG_IS_HORIZONTAL[i] else 0
        score = float(patch.mean(axis=axis).max())
        state.append(1 if score >= min_fill else 0)
        margins.append(abs(score - min_fill))
    out = SEG_TABLE.get(tuple(state))
    conf = float(np.clip(np.mean(margins) * 2.5, 0.0, 1.0))
    if out is None:
        return "?", 0.0
    return out, conf


def split_digits(bw, min_gap_ratio=0.12):
    """Split a binary strip into digit boxes using a column projection.

    Works on real 7-segment displays where the segments of one digit are
    physically separated, which breaks a plain contour based split.
    """
    h, w = bw.shape[:2]
    col = (bw > 0).sum(axis=0)
    active = col > max(1, int(0.03 * h))
    runs, start = [], None
    for i, a in enumerate(active):
        if a and start is None:
            start = i
        elif not a and start is not None:
            runs.append([start, i])
            start = None
    if start is not None:
        runs.append([start, len(active)])

    min_gap = max(2, int(min_gap_ratio * h))
    merged = []
    for r in runs:
        if merged and r[0] - merged[-1][1] < min_gap:
            merged[-1][1] = r[1]
        else:
            merged.append(list(r))

    boxes = []
    for x0, x1 in merged:
        if x1 - x0 < max(2, int(0.04 * h)):
            continue
        sub = bw[:, x0:x1] > 0
        rows = np.where(sub.sum(axis=1) > 0)[0]
        if rows.size == 0:
            continue
        y0, y1 = int(rows[0]), int(rows[-1]) + 1
        if (y1 - y0) < 0.30 * h:
            continue
        boxes.append((x0, y0, x1, y1))
    if boxes:
        tallest = max(b[3] - b[1] for b in boxes)
        boxes = [b for b in boxes if (b[3] - b[1]) > 0.55 * tallest]
    return boxes


def task_digits(crop, roi):
    """Read a 7-segment / LCD number. Returns (value, confidence, debug image)."""
    p = roi.get("params") or {}
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    bw = binarize(gray, p)
    h, w = bw.shape[:2]
    dbg = cv2.cvtColor(bw, cv2.COLOR_GRAY2BGR)
    min_fill = float(p.get("min_fill", 0.45))
    n_digits = int(p.get("digits", 0))
    boxes = []

    if p.get("split", "auto") == "auto":
        boxes = split_digits(bw, float(p.get("min_gap_ratio", 0.12)))
        if n_digits and len(boxes) != n_digits:
            boxes = []  # wrong digit count -> fall back to an even split
    if not boxes:  # fixed split fallback
        n = n_digits if n_digits > 0 else 4
        cw = w // n
        boxes = [(i * cw, 0, (i + 1) * cw, h) for i in range(n)]

    text, confs = "", []
    for b in boxes:
        c, cf = _read_cell(bw, b, min_fill)
        text += c
        confs.append(cf)
        cv2.rectangle(dbg, (b[0], b[1]), (b[2], b[3]), (129, 204, 230), 1)   # TESR gold

    if n_digits and len(text.replace("-", "")) != n_digits:
        confs.append(0.0)

    dec = int(p.get("decimals", 0))
    if dec > 0 and len(text) > dec and "?" not in text:
        text = text[:-dec] + "." + text[-dec:]

    value = text
    if p.get("as_number", True) and text and "?" not in text:
        try:
            value = float(text) if "." in text else int(text)
        except ValueError:
            value = text
    conf = float(np.mean(confs)) if confs else 0.0
    return value, conf, dbg


# --------------------------------------------------------------------------
# task 2 : OCR text (optional engines)
# --------------------------------------------------------------------------

_OCR = {"engine": None, "obj": None}


def task_ocr(crop, roi):
    p = roi.get("params") or {}
    engine = p.get("engine", "tesseract")
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    if p.get("preprocess", True):
        gray = binarize(gray, {"threshold_mode": p.get("threshold_mode", "otsu"),
                               "invert": p.get("invert", False),
                               "blur": p.get("blur", 3)})
    dbg = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    if engine == "tesseract":
        try:
            import pytesseract
        except ImportError:
            return "", 0.0, dbg
        cfg = "--psm %s" % p.get("psm", 7)
        wl = p.get("whitelist", "")
        if wl:
            cfg += " -c tessedit_char_whitelist=%s" % wl
        txt = pytesseract.image_to_string(gray, lang=p.get("lang", "eng"), config=cfg)
        return txt.strip(), 0.5, dbg

    if engine == "easyocr":
        try:
            import easyocr
        except ImportError:
            return "", 0.0, dbg
        if _OCR["engine"] != "easyocr":
            langs = (p.get("lang", "en") or "en").split("+")
            _OCR["obj"] = easyocr.Reader(langs, gpu=bool(p.get("gpu", False)))
            _OCR["engine"] = "easyocr"
        res = _OCR["obj"].readtext(crop)
        if not res:
            return "", 0.0, dbg
        txt = " ".join(r[1] for r in res)
        conf = float(np.mean([r[2] for r in res]))
        return txt.strip(), conf, dbg

    return "", 0.0, dbg


# --------------------------------------------------------------------------
# task 2b : licence plate  =  ROI -> OCR, with the plate alphabet enforced
# --------------------------------------------------------------------------

PLATE_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

# Characters OCR confuses when it cannot tell a letter slot from a digit slot.
# Only tried when the ROI carries a "pattern", so a swap is either confirmed by
# the plate format or not made at all.
PLATE_SWAPS = {"O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "Z": "2",
               "S": "5", "B": "8", "G": "6",
               "0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G"}


def plate_prep(crop, p):
    """Make a plate legible to OCR: upscale, even out the light, binarise.

    A plate in a traffic or gate camera is usually 20-40 px tall, which is well
    under what Tesseract needs. Upscaling first and equalising afterwards does
    far more for the reading than any parameter tuning later.
    """
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    target = int(p.get("plate_height", 96) or 0)
    if target > 0 and gray.shape[0] > 0 and gray.shape[0] < target:
        f = float(target) / gray.shape[0]
        gray = cv2.resize(gray, (max(1, int(gray.shape[1] * f)), target),
                          interpolation=cv2.INTER_CUBIC)
    if p.get("denoise", True):
        gray = cv2.bilateralFilter(gray, 5, 40, 40)
    if p.get("clahe", True):
        gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    if not p.get("preprocess", True):
        return gray
    mode = p.get("threshold_mode", "otsu")
    if mode == "adaptive":
        bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, 25, 9)
    else:
        _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Tesseract wants dark characters on a light plate. Decide which way round
    # this image is from the border, which is background on every plate.
    border = np.concatenate([bw[0, :], bw[-1, :], bw[:, 0], bw[:, -1]])
    if float(np.mean(border)) < 127:
        bw = 255 - bw
    if p.get("invert"):
        bw = 255 - bw
    return bw


def plate_clean(text, p):
    """Keep only plate characters, then check the result against the format.

    Returns (plate, reason). An empty plate with a reason is the honest answer
    when the frame does not contain something that looks like a plate: a wrong
    plate number in a gate log is worse than no plate number.
    """
    keep = set(p.get("whitelist") or PLATE_CHARS)
    raw = (text or "").upper()
    if p.get("keep_spaces"):
        keep.add(" ")
    s = "".join(ch for ch in raw if ch in keep)
    s = " ".join(s.split())
    lo, hi = int(p.get("min_chars", 4)), int(p.get("max_chars", 12))
    if len(s.replace(" ", "")) < lo:
        return "", "too few characters"
    if len(s.replace(" ", "")) > hi:
        return "", "too many characters"

    pattern = p.get("pattern") or ""
    if not pattern:
        return s, ""
    import re
    try:
        rx = re.compile(pattern)
    except re.error:
        return s, ""                       # a broken pattern must not block the read
    if rx.match(s):
        return s, ""
    # one confusable character at a time, accepted only if the format then fits
    for i, ch in enumerate(s):
        alt = PLATE_SWAPS.get(ch)
        if not alt:
            continue
        cand = s[:i] + alt + s[i + 1:]
        if rx.match(cand):
            return cand, ""
    return "", "does not match the plate format"


def task_plate(crop, roi):
    p = roi.get("params") or {}
    img = plate_prep(crop, p)
    dbg = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    sub = dict(p)
    sub.setdefault("psm", 7)               # one line of text, which a plate is
    sub.setdefault("whitelist", PLATE_CHARS)
    sub["preprocess"] = False              # already done, and better, above
    text, conf, _ = task_ocr(img, {"params": sub})
    plate, reason = plate_clean(text, p)
    if not plate:
        return "", 0.0, dbg
    return plate, conf, dbg


# --------------------------------------------------------------------------
# task 3 : YOLO detect / classify (ultralytics)
# --------------------------------------------------------------------------

_MODELS = {}
_ORT = {}


def _letterbox(img, size, pad=114):
    """Resize keeping aspect ratio and pad to a square. Mirrors the browser tool
    exactly, so a box seen in TESR ROI Studio lands in the same place here."""
    h, w = img.shape[:2]
    scale = min(size / float(w), size / float(h))
    nw, nh = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), pad, np.uint8)
    dx, dy = (size - nw) // 2, (size - nh) // 2
    canvas[dy:dy + nh, dx:dx + nw] = resized
    blob = canvas[:, :, ::-1].astype(np.float32) / 255.0        # BGR -> RGB, 0..1
    blob = np.transpose(blob, (2, 0, 1))[None]                  # NCHW
    return np.ascontiguousarray(blob), scale, dx, dy


def _nms(boxes, scores, classes, iou_thr):
    order = np.argsort(-scores)
    keep = []
    while order.size:
        i = order[0]
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        area_r = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        iou = inter / np.maximum(1e-9, area_i + area_r - inter)
        order = rest[(iou <= iou_thr) | (classes[rest] != classes[i])]
    return keep


def get_onnx(path):
    """Load an ONNX model with onnxruntime only. Much lighter than Ultralytics,
    which matters on a Raspberry Pi."""
    if path in _ORT:
        return _ORT[path]
    try:
        import onnxruntime as ort
    except ImportError:
        log("onnxruntime not installed -> pip install onnxruntime", "WARN")
        _ORT[path] = None
        return None
    if not os.path.isfile(path):
        log("model file not found: %s" % path, "WARN")
        _ORT[path] = None
        return None
    providers = ["CPUExecutionProvider"]
    avail = ort.get_available_providers()
    for p in ("CUDAExecutionProvider", "TensorrtExecutionProvider"):
        if p in avail:
            providers.insert(0, p)
    log("loading ONNX model: %s  (%s)" % (path, providers[0]))
    _ORT[path] = ort.InferenceSession(path, providers=providers)
    return _ORT[path]


def onnx_detect(crop, roi):
    m = roi.get("model") or {}
    sess = get_onnx(m.get("path", ""))
    dbg = crop.copy()
    if sess is None:
        return None, 0.0, dbg
    size = int(m.get("imgsz", 640))
    blob, scale, dx, dy = _letterbox(crop, size)
    out = sess.run(None, {sess.get_inputs()[0].name: blob})[0]
    pred = np.squeeze(out)                       # (4+nc, N) or (N, 4+nc)
    if pred.shape[0] < pred.shape[1]:
        pred = pred.T                            # -> (N, 4+nc)
    nc = pred.shape[1] - 4
    scores = pred[:, 4:4 + nc]
    cls = np.argmax(scores, axis=1)
    conf = scores[np.arange(len(cls)), cls]
    keep = conf >= float(m.get("conf", 0.25))
    pred, cls, conf = pred[keep], cls[keep], conf[keep]

    names = m.get("class_names") or []
    items = []
    if len(pred):
        cx, cy, bw, bh = pred[:, 0], pred[:, 1], pred[:, 2], pred[:, 3]
        boxes = np.stack([(cx - bw / 2 - dx) / scale, (cy - bh / 2 - dy) / scale,
                          (cx + bw / 2 - dx) / scale, (cy + bh / 2 - dy) / scale], axis=1)
        for i in _nms(boxes, conf, cls, float(m.get("iou", 0.45))):
            name = names[cls[i]] if cls[i] < len(names) else str(int(cls[i]))
            wanted = m.get("classes") or []
            if wanted and name not in wanted:
                continue
            x1, y1, x2, y2 = [int(v) for v in boxes[i]]
            items.append({"class": name, "conf": round(float(conf[i]), 3),
                          "box": [x1, y1, x2, y2]})
            cv2.rectangle(dbg, (x1, y1), (x2, y2), (6, 0, 243), 2)
            cv2.putText(dbg, "%s %.2f" % (name, conf[i]), (x1, max(12, y1 - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (6, 0, 243), 1, cv2.LINE_AA)

    p = roi.get("params") or {}
    mode = p.get("output", "count")
    value = len(items) if mode == "count" else \
        sorted({i["class"] for i in items}) if mode == "classes" else items
    mean = float(np.mean([i["conf"] for i in items])) if items else 0.0
    return value, mean, dbg


def onnx_classify(crop, roi):
    m = roi.get("model") or {}
    sess = get_onnx(m.get("path", ""))
    dbg = crop.copy()
    if sess is None:
        return None, 0.0, dbg
    size = int(m.get("imgsz", 224))
    blob, _, _, _ = _letterbox(crop, size)
    out = np.squeeze(sess.run(None, {sess.get_inputs()[0].name: blob})[0]).astype(np.float64)
    if out.min() < 0 or out.max() > 1 or abs(out.sum() - 1) > 0.05:
        e = np.exp(out - out.max())
        out = e / e.sum()
    idx = int(np.argmax(out))
    names = m.get("class_names") or []
    return (names[idx] if idx < len(names) else str(idx)), float(out[idx]), dbg


def get_yolo(path):
    """Load a model once and keep it in memory (never load inside the loop)."""
    if path in _MODELS:
        return _MODELS[path]
    try:
        from ultralytics import YOLO
    except ImportError:
        log("ultralytics not installed -> pip install ultralytics", "WARN")
        _MODELS[path] = None
        return None
    if not os.path.isfile(path) and not path.endswith(".pt"):
        log("model file not found: %s" % path, "WARN")
    log("loading model: %s" % path)
    _MODELS[path] = YOLO(path)
    return _MODELS[path]


def task_yolo_detect(crop, roi):
    m = roi.get("model") or {}
    if str(m.get("path", "")).lower().endswith(".onnx"):
        return onnx_detect(crop, roi)
    model = get_yolo(m.get("path", "yolov8n.pt"))
    dbg = crop.copy()
    if model is None:
        return None, 0.0, dbg
    res = model.predict(crop, conf=float(m.get("conf", 0.25)),
                        iou=float(m.get("iou", 0.45)),
                        imgsz=int(m.get("imgsz", 640)), verbose=False)[0]
    keep = m.get("classes") or []
    items, confs = [], []
    for b in res.boxes:
        cid = int(b.cls[0])
        name = res.names.get(cid, str(cid))
        if keep and name not in keep and cid not in keep:
            continue
        cf = float(b.conf[0])
        x1, y1, x2, y2 = [int(v) for v in b.xyxy[0]]
        items.append({"class": name, "conf": round(cf, 3), "box": [x1, y1, x2, y2]})
        confs.append(cf)
        cv2.rectangle(dbg, (x1, y1), (x2, y2), (6, 0, 243), 2)               # TESR red
        cv2.putText(dbg, "%s %.2f" % (name, cf), (x1, max(12, y1 - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (6, 0, 243), 1, cv2.LINE_AA)
    p = roi.get("params") or {}
    if p.get("output", "count") == "count":
        value = len(items)
    elif p.get("output") == "classes":
        value = sorted({i["class"] for i in items})
    else:
        value = items
    return value, (float(np.mean(confs)) if confs else 0.0), dbg


def task_yolo_classify(crop, roi):
    m = roi.get("model") or {}
    if str(m.get("path", "")).lower().endswith(".onnx"):
        return onnx_classify(crop, roi)
    model = get_yolo(m.get("path", "yolov8n-cls.pt"))
    dbg = crop.copy()
    if model is None:
        return None, 0.0, dbg
    res = model.predict(crop, imgsz=int(m.get("imgsz", 224)), verbose=False)[0]
    probs = res.probs
    if probs is None:
        return None, 0.0, dbg
    idx = int(probs.top1)
    return res.names.get(idx, str(idx)), float(probs.top1conf), dbg


# --------------------------------------------------------------------------
# task 4 : barcode / QR
# --------------------------------------------------------------------------

_QR = cv2.QRCodeDetector()
_BAR = cv2.barcode.BarcodeDetector() if hasattr(cv2, "barcode") else None

# ---- 1-D symbologies, same tables and logic as TESR ROI Studio -----------
C128_PATTERNS = [
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312",
    "132212", "221213", "221312", "231212", "112232", "122132", "122231", "113222",
    "123122", "123221", "223211", "221132", "221231", "213212", "223112", "312131",
    "311222", "321122", "321221", "312212", "322112", "322211", "212123", "212321",
    "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121",
    "313121", "211331", "231131", "213113", "213311", "213131", "311123", "311321",
    "331121", "312113", "312311", "332111", "314111", "221411", "431111", "111224",
    "111422", "121124", "121421", "141122", "141221", "112214", "112412", "122114",
    "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112",
    "421211", "212141", "214121", "412121", "111143", "111341", "131141", "114113",
    "114311", "411113", "411311", "113141", "114131", "311141", "411131", "211412",
    "211214", "211232", "2331112",
]
C128_STOP = 106

C39 = {
    "0": "nnnwwnwnn", "1": "wnnwnnnnw", "2": "nnwwnnnnw", "3": "wnwwnnnnn",
    "4": "nnnwwnnnw", "5": "wnnwwnnnn", "6": "nnwwwnnnn", "7": "nnnwnnwnw",
    "8": "wnnwnnwnn", "9": "nnwwnnwnn", "A": "wnnnnwnnw", "B": "nnwnnwnnw",
    "C": "wnwnnwnnn", "D": "nnnnwwnnw", "E": "wnnnwwnnn", "F": "nnwnwwnnn",
    "G": "nnnnnwwnw", "H": "wnnnnwwnn", "I": "nnwnnwwnn", "J": "nnnnwwwnn",
    "K": "wnnnnnnww", "L": "nnwnnnnww", "M": "wnwnnnnwn", "N": "nnnnwnnww",
    "O": "wnnnwnnwn", "P": "nnwnwnnwn", "Q": "nnnnnnwww", "R": "wnnnnnwwn",
    "S": "nnwnnnwwn", "T": "nnnnwnwwn", "U": "wwnnnnnnw", "V": "nwwnnnnnw",
    "W": "wwwnnnnnn", "X": "nwnnwnnnw", "Y": "wwnnwnnnn", "Z": "nwwnwnnnn",
    "-": "nwnnnnwnw", ".": "wwnnnnwnn", " ": "nwwnnnwnn", "*": "nwnnwnwnn",
    "$": "nwnwnwnnn", "/": "nwnwnnnwn", "+": "nwnnnwnwn", "%": "nnnwnwnwn",
}
C39_REV = {v: k for k, v in C39.items()}

EAN_L = ["0001101", "0011001", "0010011", "0111101", "0100011",
         "0110001", "0101111", "0111011", "0110111", "0001011"]
EAN_G = ["".join("1" if c == "0" else "0" for c in s[::-1]) for s in EAN_L]
EAN_R = ["".join("1" if c == "0" else "0" for c in s) for s in EAN_L]
EAN_PARITY = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG",
              "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"]
ITF_DIGIT = ["nnwwn", "wnnnw", "nwnnw", "wwnnn", "nnwnw",
             "wnwnn", "nwwnn", "nnnww", "wnnwn", "nwnwn"]

C128_CTRL = "".join(chr(i) for i in range(32))


def _c128_char(cset, code):
    if code < 0 or code > 102:
        return None
    if cset == "C":
        return "%02d" % code if code <= 99 else None
    if code <= 95:
        if cset == "B":
            return chr(code + 32)
        return chr(code + 32) if code < 64 else C128_CTRL[code - 64]
    return ""


def _widths_to_pattern(widths, modules):
    """Round bar widths to module counts, nudging until the total matches.
    Independent rounding falls apart once a module is only 2-3 px wide."""
    total_px = float(sum(widths))
    if total_px <= 0:
        return None
    unit = total_px / modules
    exact = [w / unit for w in widths]
    n = [max(1, min(4, int(round(v)))) for v in exact]
    total = sum(n)
    guard = 0
    while total != modules and guard < 12:
        guard += 1
        up = total < modules
        best_i, best_err = -1, -1e9
        for i in range(len(n)):
            if up and n[i] >= 4:
                continue
            if not up and n[i] <= 1:
                continue
            err = (exact[i] - n[i]) if up else (n[i] - exact[i])
            if err > best_err:
                best_err, best_i = err, i
        if best_i < 0:
            return None
        n[best_i] += 1 if up else -1
        total += 1 if up else -1
    if total != modules:
        return None
    for i in range(len(n)):
        if abs(exact[i] - n[i]) > 0.65:
            return None
    return "".join(str(v) for v in n)


def _binarize_row(gray, y):
    row = gray[y]
    lo, hi = int(row.min()), int(row.max())
    if hi - lo < 30:
        return None
    return (row < (lo + hi) / 2.0).astype(np.uint8)   # 1 = bar


def _runs_of(row):
    changes = np.flatnonzero(np.diff(row)) + 1
    edges = np.concatenate(([0], changes, [len(row)]))
    return np.diff(edges).tolist(), bool(row[0])


def _trim_quiet(runs, first_is_bar):
    r = list(runs)
    if not first_is_bar:
        r = r[1:]
    if r and (len(r) - 1) % 2:
        r = r[:-1]
    return r


def _decode_c128(runs):
    if len(runs) < 24:
        return None
    codes, i = [], 0
    while i + 6 <= len(runs):
        pat = _widths_to_pattern(runs[i:i + 6], 11)
        if pat is None:
            break
        try:
            code = C128_PATTERNS.index(pat)
        except ValueError:
            break
        if code > 105:
            break
        codes.append(code)
        i += 6
        if code == C128_STOP:
            break
    if len(codes) < 4:
        return None
    if codes[-1] != C128_STOP:
        if _widths_to_pattern(runs[i:i + 7], 13) != "2331112":
            return None
        codes.append(C128_STOP)
    if not 103 <= codes[0] <= 105:
        return None
    cset = ["A", "B", "C"][codes[0] - 103]
    payload, check = codes[:-2], codes[-2]
    total = payload[0] + sum(k * payload[k] for k in range(1, len(payload)))
    if total % 103 != check:
        return None
    text = ""
    for c in payload[1:]:
        if c == 99:
            cset = "C"
            continue
        if c == 100:
            cset = "A" if cset == "B" else "B"
            continue
        if c == 101:
            cset = "B" if cset == "A" else "A"
            continue
        ch = _c128_char(cset, c)
        if ch is None:
            return None
        text += ch
    return (text, "code_128") if text else None


def _decode_c39(runs):
    chars, i = [], 0
    while i + 9 <= len(runs):
        w = runs[i:i + 9]
        mx, mn = max(w), min(w)
        if mn <= 0 or mx / float(mn) < 1.5:
            return None
        mid = (mx + mn) / 2.0
        key = "".join("w" if v > mid else "n" for v in w)
        ch = C39_REV.get(key)
        if ch is None:
            break
        chars.append(ch)
        i += 10                                    # 9 elements + inter-character gap
    if len(chars) < 3 or chars[0] != "*" or chars[-1] != "*":
        return None
    text = "".join(chars[1:-1])
    return (text, "code_39") if text else None


def _decode_ean(row):
    w = len(row)
    dark = np.flatnonzero(row)
    if dark.size < 20:
        return None
    x0, x1 = int(dark[0]), int(dark[-1])
    span = x1 - x0 + 1
    for modules in (95, 67):
        unit = span / float(modules)
        if unit < 0.8:
            continue
        idx = np.clip((x0 + (np.arange(modules) + 0.5) * unit).round().astype(int), 0, w - 1)
        bits = "".join("1" if row[i] else "0" for i in idx)
        n = 6 if modules == 95 else 4
        mid = 3 + n * 7
        if bits[:3] != "101" or bits[mid:mid + 5] != "01010" or bits[-3:] != "101":
            continue
        left, parity = "", ""
        for d in range(n):
            chunk = bits[3 + d * 7:10 + d * 7]
            if chunk in EAN_L:
                left += str(EAN_L.index(chunk)); parity += "L"
            elif chunk in EAN_G:
                left += str(EAN_G.index(chunk)); parity += "G"
            else:
                return None
        right = ""
        for d in range(n):
            chunk = bits[mid + 5 + d * 7:mid + 12 + d * 7]
            if chunk not in EAN_R:
                return None
            right += str(EAN_R.index(chunk))
        if modules == 95:
            if parity not in EAN_PARITY:
                return None
            text = str(EAN_PARITY.index(parity)) + left + right
        else:
            if parity != "LLLL":
                return None
            text = left + right
        ds = [int(c) for c in text]
        if len(ds) == 13:
            chk = sum(d * (1 if i % 2 == 0 else 3) for i, d in enumerate(ds[:-1]))
        else:
            chk = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(ds[:-1]))
        if (10 - chk % 10) % 10 != ds[-1]:
            return None
        is_upc = len(text) == 13 and text[0] == "0"
        fmt = ("upc_a" if is_upc else "ean_13") if len(text) == 13 else "ean_8"
        return (text[1:] if is_upc else text), fmt
    return None


def _decode_itf(runs):
    if len(runs) < 14:
        return None
    body = runs[4:len(runs) - 3]
    body = body[:len(body) // 10 * 10]
    if not body:
        return None
    mn, mx = min(body), max(body)
    if mn <= 0 or mx / float(mn) < 1.5:
        return None
    mid = (mn + mx) / 2.0
    text = ""
    for i in range(0, len(body), 10):
        chunk = body[i:i + 10]
        bars = "".join("w" if chunk[k] > mid else "n" for k in range(0, 10, 2))
        spaces = "".join("w" if chunk[k] > mid else "n" for k in range(1, 10, 2))
        if bars not in ITF_DIGIT or spaces not in ITF_DIGIT:
            return None
        text += str(ITF_DIGIT.index(bars)) + str(ITF_DIGIT.index(spaces))
    return (text, "itf") if len(text) >= 4 else None


def decode_barcode_1d(gray, lines=15, min_votes=2):
    """Scan several rows in both polarities and require two independent rows to
    agree. Every symbology here is checksum-validated, so a miss is far more
    likely than a misread."""
    h, w = gray.shape[:2]
    sources = [gray]
    if w < 700:
        f = int(np.ceil(700.0 / max(1, w)))
        sources.append(cv2.resize(gray, (w * f, h), interpolation=cv2.INTER_NEAREST))

    for src in sources:
        for invert in (False, True):
            votes = {}
            sh, sw = src.shape[:2]
            for i in range(lines):
                y = int((i + 0.5) * sh / lines)
                row = _binarize_row(src, y)
                if row is None:
                    continue
                if invert:
                    row = 1 - row
                got = _decode_ean(row)
                if got:
                    votes[got] = votes.get(got, 0) + 1
                runs, first_is_bar = _runs_of(row)
                if len(runs) < 8:
                    continue
                trimmed = _trim_quiet(runs, first_is_bar)
                for fn in (_decode_c128, _decode_c39, _decode_itf):
                    got = fn(trimmed)
                    if got:
                        votes[got] = votes.get(got, 0) + 1
            if votes:
                (text, fmt), n = max(votes.items(), key=lambda kv: kv[1])
                if n >= min_votes:
                    return text, fmt, n
    return None


def task_code(crop, roi):
    """QR and 1-D barcodes using only OpenCV plus the decoder above.
    pyzbar is used if present but is not required."""
    p = roi.get("params") or {}
    dbg = crop.copy()
    found = []

    if p.get("qr", True):
        try:
            data, pts, _ = _QR.detectAndDecode(crop)
            if data:
                found.append(data)
                if pts is not None:
                    cv2.polylines(dbg, [pts.astype(int)], True, TESR_GOLD, 2)
        except cv2.error:
            pass

    if p.get("barcode", True) and not found and _BAR is not None:
        try:
            ok, infos, _types, corners = _BAR.detectAndDecodeWithType(crop)
            if ok and infos:
                for txt, pts in zip(infos, corners):
                    if txt:
                        found.append(txt)
                        cv2.polylines(dbg, [pts.astype(int)], True, TESR_GOLD, 2)
        except (cv2.error, ValueError):
            pass

    if p.get("barcode", True) and not found:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        got = decode_barcode_1d(gray)
        if got:
            found.append(got[0])

    if p.get("barcode", True) and not found:
        try:
            from pyzbar import pyzbar
            for b in pyzbar.decode(crop):
                found.append(b.data.decode("utf-8", "ignore"))
        except ImportError:
            pass

    value = found[0] if found else ""
    return value, 1.0 if found else 0.0, dbg


# --------------------------------------------------------------------------
# task 5 : presence / change detection
# --------------------------------------------------------------------------

_REF = {}


def task_presence(crop, roi):
    p = roi.get("params") or {}
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    key = roi["id"]
    ref = _REF.get(key)
    if ref is None or ref.shape != gray.shape:
        _REF[key] = gray
        return False, 0.0, crop.copy()
    diff = cv2.absdiff(ref, gray)
    _, mask = cv2.threshold(diff, int(p.get("sensitivity", 25)), 255, cv2.THRESH_BINARY)
    ratio = float(np.count_nonzero(mask)) / mask.size
    if p.get("reference", "first") == "previous":
        _REF[key] = gray
    dbg = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    return bool(ratio >= float(p.get("min_ratio", 0.05))), min(1.0, ratio * 5), dbg


# --------------------------------------------------------------------------
# task 6 : colour check
# --------------------------------------------------------------------------

def task_color(crop, roi):
    p = roi.get("params") or {}
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h_min, h_max = int(p.get("h_min", 0)), int(p.get("h_max", 179))
    s_min, s_max = int(p.get("s_min", 80)), int(p.get("s_max", 255))
    v_min, v_max = int(p.get("v_min", 80)), int(p.get("v_max", 255))

    # Hue is a CIRCLE, and red sits on the seam: half of red is just above 0
    # and half just below 179. So when h_min is ABOVE h_max the band runs off
    # the top and comes back through 0, and it takes two ranges OR-ed
    # together. A red lamp set to 0-10 alone reads false whenever its hue
    # lands on the far side of the seam - which is most red lamps, most days.
    if h_min <= h_max:
        mask = cv2.inRange(hsv,
                           np.array([h_min, s_min, v_min]),
                           np.array([h_max, s_max, v_max]))
    else:
        mask = cv2.bitwise_or(
            cv2.inRange(hsv,
                        np.array([h_min, s_min, v_min]),
                        np.array([179, s_max, v_max])),
            cv2.inRange(hsv,
                        np.array([0, s_min, v_min]),
                        np.array([h_max, s_max, v_max])))
    ratio = float(np.count_nonzero(mask)) / mask.size
    ok = ratio >= float(p.get("min_ratio", 0.20))
    return (ok if p.get("output", "bool") == "bool" else round(ratio, 4)), ratio, \
        cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)


# --------------------------------------------------------------------------
# task 7 : analogue needle gauge
# --------------------------------------------------------------------------
#
# The dial equivalent of level_line: a geometric reading - here an angle rather
# than a position along a line - turned into engineering units by two reference
# points the user supplies. On a tank you give the value at each end of the
# line; on a dial you give the value at each end of the sweep.
#
# How the needle is found, in one sentence: sweep a ray out from the hub in
# every direction and keep the direction in which dark pixels run furthest.
# A needle is the only mark on a dial that is both long and joined to the hub,
# so the tick marks and the printed numbers near the rim never win - they are
# not connected to the centre. No model, no Hough transform, nothing to tune
# beyond a threshold and how long a needle has to be before it is believed.

_GAUGE_STEP = 0.5     # degrees per ray; 720 rays round the dial
_GAUGE_GAP = 2        # samples of background a ray may cross and still continue
_GAUGE_OPP = 50       # rays either side of "straight across" when the pointer
                      # is the short end of a counterweighted needle (+/- 25 deg)


def gauge_reach(mask, cx, cy, radius, inner, outer):
    """For every direction, how far out from the hub the needle pixels run.

    Returns an array of 720 reaches, each a fraction of `radius`, and the step
    in degrees between them. Vectorised because a Raspberry Pi should not be
    asked to walk 720 rays in a Python loop every frame.
    """
    h, w = mask.shape[:2]
    r0 = max(1.0, inner * radius)
    r1 = max(r0 + 1.0, outer * radius)
    steps = np.arange(r0, r1 + 1.0, 1.0)                   # one sample per pixel
    ang = np.radians(np.arange(0.0, 360.0, _GAUGE_STEP))
    # screen y grows downwards, so sin is negated: 90 degrees points up
    xs = np.clip(np.rint(cx + np.cos(ang)[:, None] * steps[None, :]), 0, w - 1).astype(np.intp)
    ys = np.clip(np.rint(cy - np.sin(ang)[:, None] * steps[None, :]), 0, h - 1).astype(np.intp)
    hit = mask[ys, xs]                                     # (rays, samples) bool

    # A ray stops at the first run of _GAUGE_GAP+1 background samples: that is
    # where the needle ended. Anything beyond it belongs to some other mark.
    miss = ~hit
    n = miss.shape[1]
    if n > _GAUGE_GAP:
        trip = miss[:, _GAUGE_GAP:].copy()
        for k in range(1, _GAUGE_GAP + 1):
            trip &= miss[:, _GAUGE_GAP - k:n - k]
        first = np.where(trip.any(axis=1), trip.argmax(axis=1), n)
    else:
        first = np.full(miss.shape[0], n)
    limit = first[:, None]                                 # samples before the gap
    idx = np.arange(n)[None, :]
    best = np.where(hit & (idx < limit), idx, -1).max(axis=1)
    reach = np.where(best >= 0, (r0 + best) / radius, 0.0)
    return reach, _GAUGE_STEP


def gauge_angle(reach, step, around=None):
    """The direction of the needle, in degrees, 0 = right and anticlockwise up.

    The needle is several rays wide, so the answer is the weighted mean of the
    one run of rays around the winner - one run, not every ray that scores
    well, or a needle with a tail would be averaged with its own back end.

    `around` restricts the search to a window of rays, which is how the back
    end of a counterweighted needle is measured: look opposite the front one.
    """
    n = len(reach)
    if around is None:
        i = int(np.argmax(reach))
    else:
        win = (np.arange(around - _GAUGE_OPP, around + _GAUGE_OPP + 1)) % n
        i = int(win[int(np.argmax(reach[win]))])
    top = float(reach[i])
    if top <= 0.0:
        return None, 0.0, 0.0
    edge = top * 0.9
    half = n // 4                      # never let one run swallow the dial
    lo = i
    while reach[(lo - 1) % n] >= edge and i - lo < half:
        lo -= 1
    hi = i
    while reach[(hi + 1) % n] >= edge and hi - i < half:
        hi += 1
    run = np.arange(lo, hi + 1) % n
    wgt = reach[run]
    rad = np.radians(run * step)
    theta = float(np.degrees(np.arctan2(np.sum(wgt * np.sin(rad)),
                                        np.sum(wgt * np.cos(rad))))) % 360.0

    # Confidence: how far the winning run stands above the rest of the dial.
    rest = np.delete(reach, run)
    base = float(np.percentile(rest, 90)) if rest.size else 0.0
    conf = float(np.clip((top - base) / max(top, 1e-6), 0.0, 1.0))
    return theta, top, conf


def gauge_fraction(theta, a0, a1):
    """Where an angle falls along a sweep that runs from a0 to a1.

    0.0 at a0, 1.0 at a1, and the direction follows from the two numbers: the
    usual 225 -> -45 runs clockwise, a gauge written -45 -> 225 runs the other
    way, and neither needs a separate setting. Outside the sweep - the dead
    sector under the hub - the result goes below 0 or above 1, so the caller
    can clamp it or refuse it.
    """
    span = float(a1) - float(a0)
    if abs(span) < 1e-6:
        return None
    direction = 1.0 if span > 0 else -1.0
    travel = ((float(theta) - float(a0)) * direction) % 360.0   # 0 .. 360
    reach = abs(span)
    if travel <= reach:
        return travel / reach
    # past one end or short of the other: whichever is nearer
    over, under = travel - reach, 360.0 - travel
    return (1.0 + over / reach) if over <= under else (-under / reach)


def task_gauge(crop, roi):
    """Read an analogue needle gauge inside a rectangular ROI."""
    p = roi.get("params") or {}
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    h, w = gray.shape[:2]

    cx = float(p.get("center_x", 0.5)) * w
    cy = float(p.get("center_y", 0.5)) * h
    # the largest circle around that centre that still fits inside the crop
    radius = float(min(cx, w - cx, cy, h - cy))
    if radius < 8.0:
        return None, 0.0, crop.copy()

    if str(p.get("threshold_mode", "otsu")) == "otsu":
        th, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        th = float(th)
    else:
        th = float(p.get("threshold", 128))
    dark = str(p.get("needle", "dark")) != "light"
    mask = (gray < th) if dark else (gray > th)

    inner = float(p.get("scan_inner", 0.12))
    outer = float(p.get("scan_outer", 0.92))
    dbg = cv2.cvtColor((mask * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    draw_mark(dbg, int(round(cx)), int(round(cy)), 6)

    # A dial where most of the face is needle-coloured means the threshold is
    # wrong, or this is not a dial at all. Either way there is nothing to read.
    ys, xs = np.mgrid[0:h, 0:w]
    dist = np.hypot(xs - cx, ys - cy)
    ring = (dist >= inner * radius) & (dist <= outer * radius)
    face = int(np.count_nonzero(ring))
    fill = float(np.count_nonzero(mask & ring)) / max(1, face)
    if fill > float(p.get("max_fill", 0.35)):
        return None, 0.0, dbg

    reach, step = gauge_reach(mask, cx, cy, radius, inner, outer)
    theta, length, conf = gauge_angle(reach, step)
    if theta is None:
        return None, 0.0, dbg
    # A counterweighted needle has a back end as long as the pointer, sometimes
    # longer. "long" believes the longest mark joined to the hub; "short" reads
    # the other end of the same needle, straight across the dial.
    if str(p.get("pointer", "long")) == "short":
        opp = int(round(((theta + 180.0) % 360.0) / step))
        theta, length, _ = gauge_angle(reach, step, around=opp)
        if theta is None:
            return None, 0.0, dbg
        # the confidence stays the one measured for the needle as a whole: the
        # back end is shorter by design, and scoring it against its own front
        # end would read as "unsure" on every gauge built this way
    if length < float(p.get("min_length", 0.35)):
        return None, 0.0, dbg

    frac = gauge_fraction(theta, p.get("min_angle", 225.0), p.get("max_angle", -45.0))
    if frac is None:
        return None, 0.0, dbg
    if frac < -0.02 or frac > 1.02:
        if not p.get("clamp", True):
            return None, 0.0, dbg            # the needle is off the scale
    frac = float(np.clip(frac, 0.0, 1.0))

    v0 = float(p.get("min_value", 0.0))
    v1 = float(p.get("max_value", 100.0))
    value = round(v0 + (v1 - v0) * frac, int(p.get("decimals", 1)))

    rad = np.radians(theta)
    cv2.line(dbg, (int(round(cx)), int(round(cy))),
             (int(round(cx + np.cos(rad) * length * radius)),
              int(round(cy - np.sin(rad) * length * radius))), TESR_RED, 2, cv2.LINE_AA)
    return value, conf, dbg


# Tasks that measure or count across the whole frame rather than inside a crop.
# A counting line only means anything in the coordinates it was drawn on, so
# these receive the frame itself plus a context carrying the camera scale.
MODEL_TASKS = ("yolo_detect", "yolo_classify")

FRAME_TASKS = ("level_line", "measure_line", "line_count", "zone_count")


def task_level_line(frame, roi, ctx):
    return LINE.task_level(frame, roi, ctx)


def task_measure_line(frame, roi, ctx):
    return LINE.task_measure(frame, roi, ctx)


def task_line_count(frame, roi, ctx):
    counter = ctx["counters"].get(roi["id"])
    if counter is None:
        counter = ctx["counters"][roi["id"]] = LINE.LineCounter(roi)
    counter.update(ctx["tracks"], frame.shape[1], frame.shape[0])
    v = counter.value()
    key = (roi.get("params") or {}).get("report", "net")
    value = v if key == "all" else v.get(key, v["net"])
    return value, 1.0, {"counts": v}


def task_zone_count(frame, roi, ctx):
    h, w = frame.shape[:2]
    n, names = LINE.zone_count(roi, ctx["tracks"], w, h)
    return n, 1.0, {"classes": names}


TASKS = {
    "level_line": task_level_line,
    "measure_line": task_measure_line,
    "line_count": task_line_count,
    "zone_count": task_zone_count,
    "digits_7seg": task_digits,
    "ocr_text": task_ocr,
    "plate_ocr": task_plate,
    "yolo_detect": task_yolo_detect,
    "yolo_classify": task_yolo_classify,
    "barcode_qr": task_code,
    "presence_diff": task_presence,
    "color_check": task_color,
    "gauge_needle": task_gauge,
}


def detect_frame(frame, det):
    """One inference per frame, shared by every line and zone on that camera.

    Running the model once and handing the boxes to each shape is the difference
    between a Pi keeping up and a Pi falling over.
    """
    if not det or not det.get("path"):
        return []
    roi = {"model": det, "params": {"output": "items"}}
    if str(det["path"]).lower().endswith(".onnx"):
        value, _, _ = onnx_detect(frame, roi)
    else:
        value, _, _ = task_yolo_detect(frame, roi)
    return value if isinstance(value, list) else []


# --------------------------------------------------------------------------
# outputs
# --------------------------------------------------------------------------

class OutputBus:
    def __init__(self, runtime, project, cam_id):
        self.rt = runtime
        self.project = project
        self.cam_id = cam_id
        self.csv_writer = None
        self.csv_file = None
        self.jsonl = None
        self.mqtt = None
        self.last = {}

        if runtime.get("jsonl_log"):
            ensure_dir(runtime["jsonl_log"])
            self.jsonl = open(runtime["jsonl_log"], "a", encoding="utf-8")
        if runtime.get("csv_log"):
            ensure_dir(runtime["csv_log"])
            new = not os.path.isfile(runtime["csv_log"])
            self.csv_file = open(runtime["csv_log"], "a", newline="", encoding="utf-8")
            self.csv_writer = csv.writer(self.csv_file)
            if new:
                self.csv_writer.writerow(["timestamp", "camera", "roi", "key", "value", "conf"])

        mq = runtime.get("mqtt") or {}
        if mq.get("enabled"):
            try:
                import paho.mqtt.client as mqtt_client
                # paho-mqtt 2.x wants to be told which callback signatures we
                # use, and prints a DeprecationWarning in the middle of the
                # startup log if it is not told - which reads like a fault when
                # it is not one. Nothing here registers a callback (this bus
                # only publishes), so version 2 is the quiet, correct answer.
                # paho-mqtt 1.x has no such argument, hence the fallback.
                try:
                    self.mqtt = mqtt_client.Client(
                        mqtt_client.CallbackAPIVersion.VERSION2)
                except AttributeError:
                    self.mqtt = mqtt_client.Client()
                if mq.get("username"):
                    self.mqtt.username_pw_set(mq["username"], mq.get("password", ""))
                self.mqtt.connect(mq.get("host", "localhost"), int(mq.get("port", 1883)), 60)
                self.mqtt.loop_start()
                log("MQTT connected -> %s:%s" % (mq.get("host"), mq.get("port")))
            except Exception as exc:  # broker down should not kill the vision loop
                log("MQTT disabled (%s)" % exc, "WARN")
                self.mqtt = None

    def publish(self, roi, key, value, conf, changed, echo=True):
        ts = now_iso()
        rec = {"time": ts, "project": self.project, "camera": self.cam_id,
               "roi": roi["name"], "key": key, "value": value, "conf": round(conf, 3)}
        # An expected class adds an opinion; it never alters what was read, so
        # "value" above is the same whether or not anybody set an expectation.
        verdict = verdict_for(roi, value)
        if verdict:
            rec["expect"] = (roi.get("params") or {}).get("expect")
            rec["verdict"] = verdict
        if echo and self.rt.get("print_console"):
            print("%s  %-14s %-10s = %s  (conf %.2f)%s"
                  % (ts, roi["name"], key, value, conf,
                     ("  [%s]" % verdict) if verdict else ""), flush=True)
        if self.jsonl:
            self.jsonl.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self.jsonl.flush()
        if self.csv_writer:
            self.csv_writer.writerow([ts, self.cam_id, roi["name"], key, value, round(conf, 3)])
            self.csv_file.flush()
        if self.mqtt:
            topic = "%s/%s/%s" % (self.rt["mqtt"].get("topic_prefix", "tesr/vision"),
                                  self.cam_id, key)
            try:
                self.mqtt.publish(topic, json.dumps(rec, ensure_ascii=False))
            except Exception as exc:
                log("MQTT publish failed: %s" % exc, "WARN")
        wh = self.rt.get("webhook") or {}
        if wh.get("enabled") and wh.get("url") and changed:
            self._post(wh["url"], rec)
        # the student hook runs last, and a mistake in it must not stop the loop
        try:
            on_result(rec, changed)
        except Exception as exc:
            log("on_result() failed: %s" % exc, "WARN")

    def publish_event(self, name, payload, echo=True):
        """A thing that happened rather than a value that was read.

        Used for a shift rollover: the closing numbers go out on their own
        topic so a dashboard can file them against the shift that produced
        them, instead of having to notice that a counter went back to zero.
        """
        rec = {"time": now_iso(), "project": self.project, "camera": self.cam_id,
               "event": name}
        rec.update(payload or {})
        if echo:
            print("[EVENT] %s  %s" % (name, json.dumps(payload or {}, ensure_ascii=False)),
                  flush=True)
        if self.jsonl:
            self.jsonl.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self.jsonl.flush()
        if self.csv_writer:
            self.csv_writer.writerow([rec["time"], self.cam_id, payload.get("roi", ""),
                                      name, json.dumps(payload, ensure_ascii=False), ""])
            self.csv_file.flush()
        if self.mqtt:
            topic = "%s/%s/event/%s" % (self.rt["mqtt"].get("topic_prefix", "tesr/vision"),
                                        self.cam_id, name)
            try:
                self.mqtt.publish(topic, json.dumps(rec, ensure_ascii=False))
            except Exception as exc:
                log("MQTT publish failed: %s" % exc, "WARN")
        wh = self.rt.get("webhook") or {}
        if wh.get("enabled") and wh.get("url"):
            self._post(wh["url"], rec)
        # the student hook runs last, and a mistake in it must not stop the loop
        try:
            on_event(rec)
        except Exception as exc:
            log("on_event() failed: %s" % exc, "WARN")

    @staticmethod
    def _post(url, rec):
        import urllib.request  # stdlib only, no extra dependency
        try:
            req = urllib.request.Request(
                url, data=json.dumps(rec).encode("utf-8"),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=2).read()
        except Exception as exc:
            log("webhook failed: %s" % exc, "WARN")

    def close(self):
        if self.jsonl:
            self.jsonl.close()
        if self.csv_file:
            self.csv_file.close()
        if self.mqtt:
            self.mqtt.loop_stop()
            self.mqtt.disconnect()


# --------------------------------------------------------------------------
# grouping: several single-digit ROIs -> one reading
# --------------------------------------------------------------------------

def counter_state_path(rt, cam_id):
    p = ((rt.get("counter_reset") or {}).get("state_file") or "").strip()
    return p.replace("{camera}", str(cam_id)) if p else ""


def load_counter_state(path):
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception as exc:          # a corrupt file must not stop the line
        log("counter state not readable (%s) - starting from zero" % exc, "WARN")
        return {}


def save_counter_state(path, counters):
    """Write atomically: a power cut mid-write must not destroy the shift count."""
    if not path or not counters:
        return
    data = {rid: c.state() for rid, c in counters.items()}
    try:
        ensure_dir(path)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as exc:
        log("counter state not saved (%s)" % exc, "WARN")


# --------------------------------------------------------------------------
# grouping helpers
# --------------------------------------------------------------------------

def roi_center(rect):
    return (rect["x"] + rect["w"] / 2.0, rect["y"] + rect["h"] / 2.0)


def has_rect(roi):
    return roi.get("shape", "rect") == "rect" and isinstance(roi.get("rect"), dict)


def build_groups(rois, auto=False):
    """Return [(group_name, [roi, ...])] with members ordered left to right.

    A ROI joins a group either by carrying an explicit "group" field, or - when
    --auto-group is on - by sitting on the same row as its neighbours. Rows are
    found by clustering the vertical centres, so one ROI per digit still reads
    out as a single number without any hand editing.
    """
    explicit, loose = {}, []
    for r in rois:
        g = r.get("group")
        if g:
            explicit.setdefault(g, []).append(r)
        else:
            loose.append(r)

    groups = [(name, sorted(members, key=lambda r: roi_center(r["rect"])[0]))
              for name, members in explicit.items()]

    if auto and loose:
        digits = [r for r in loose if r.get("task") == "digits_7seg" and has_rect(r)]
        if len(digits) >= 2:
            heights = sorted(r["rect"]["h"] for r in digits)
            tol = max(0.01, heights[len(heights) // 2] * 0.6)
            rows = []
            for r in sorted(digits, key=lambda r: roi_center(r["rect"])[1]):
                cy = roi_center(r["rect"])[1]
                if rows and abs(cy - rows[-1][0]) <= tol:
                    rows[-1][1].append(r)
                    rows[-1][0] = sum(roi_center(x["rect"])[1] for x in rows[-1][1]) / len(rows[-1][1])
                else:
                    rows.append([cy, [r]])
            for i, (_, members) in enumerate(rows):
                if len(members) < 2:
                    continue          # a lone digit is not a reading
                groups.append(("row%d" % (i + 1),
                               sorted(members, key=lambda r: roi_center(r["rect"])[0])))
    return groups


def group_value(members, results):
    """Concatenate the members' readings. Any unreadable digit makes the whole
    reading unreliable, so it is reported rather than quietly dropped."""
    parts, ok, confs = [], True, []
    for r in members:
        v = results.get(r["id"], {}).get("value")
        c = results.get(r["id"], {}).get("conf", 0.0)
        if v is None or v == "" or (isinstance(v, str) and "?" in v):
            parts.append("?")
            ok = False
        else:
            parts.append(str(v))
        confs.append(c)
    text = "".join(parts)
    return text, ok, (float(np.mean(confs)) if confs else 0.0)


# --------------------------------------------------------------------------
# drawing
# --------------------------------------------------------------------------

# TESR corporate identity, sampled from the company logo.
# OpenCV takes BGR, so each tuple is the reverse of the hex value in the docs.
TESR_RED = (6, 0, 243)          # #F30006
TESR_GOLD = (129, 204, 230)     # #E6CC81
TESR_GOLD_DEEP = (73, 123, 146)  # #927B49
INK = (27, 27, 27)              # #1B1B1B
WHITE = (242, 242, 242)         # #F2F2F2
GREY = (154, 154, 154)          # #9A9A9A

TASK_COLOUR = {
    "digits_7seg": TESR_GOLD,
    "ocr_text": (65, 164, 217),      # #D9A441
    "yolo_detect": TESR_RED,
    "yolo_classify": (107, 122, 255),  # #FF7A6B
    "barcode_qr": (224, 211, 127),   # #7FD3E0
    "presence_diff": (127, 201, 134),  # #86C97F
    "color_check": (216, 155, 200),  # #C89BD8
    "gauge_needle": (90, 190, 224),  # #E0BE5A
    "level_line": (224, 180, 90),    # water blue-gold
    "measure_line": TESR_GOLD,
    "line_count": (110, 220, 250),   # counting gate
    "zone_count": (150, 210, 140),   # occupancy zone
}


def count_text(roi, v):
    """A directional counter as something readable at a glance across a room."""
    p = roi.get("params") or {}
    a, b = p.get("label_in", "in"), p.get("label_out", "out")
    bits = []
    for k in (a, b):
        if k in v:
            bits.append("%s %s" % (k.upper(), v[k]))
    if "net" in v:
        bits.append("net %s" % v["net"])
    return "  ".join(bits) or "-"


def draw_mark(img, x, y, r):
    """Small TESR emblem: gold ring, dark core, red centre dot."""
    cv2.circle(img, (x, y), r, TESR_GOLD_DEEP, 1, cv2.LINE_AA)
    cv2.circle(img, (x, y), max(1, r - 3), INK, -1, cv2.LINE_AA)
    cv2.circle(img, (x, y), max(1, r - 3), TESR_GOLD, 1, cv2.LINE_AA)
    cv2.circle(img, (x, y), max(1, r // 3), TESR_RED, -1, cv2.LINE_AA)


def _text(img, s, org, scale, colour, thick=1):
    cv2.putText(img, s, org, cv2.FONT_HERSHEY_SIMPLEX, scale, colour, thick, cv2.LINE_AA)


def _panel(img, x, y, w, h, alpha=0.78):
    """Dark translucent block so the panel stays readable over any scene."""
    x, y = max(0, x), max(0, y)
    w = min(w, img.shape[1] - x)
    h = min(h, img.shape[0] - y)
    if w <= 0 or h <= 0:
        return
    roi = img[y:y + h, x:x + w]
    roi[:] = cv2.addWeighted(roi, 1 - alpha, np.full_like(roi, INK, np.uint8), alpha, 0)


def _text_w(txt, scale):
    """How wide this string will actually be, in pixels, at this scale."""
    return cv2.getTextSize(str(txt), cv2.FONT_HERSHEY_SIMPLEX, scale, 1)[0][0]


def _fit_text(txt, max_w, scale):
    """Cut to the space measured for it, not to a guessed character count.

    A character count cannot know that "Green_Apple" is wider than "48.6" at
    the same length, which is how a class name used to run straight through the
    confidence printed beside it.
    """
    txt = str(txt)
    if _text_w(txt, scale) <= max_w:
        return txt
    while txt and _text_w(txt + "..", scale) > max_w:
        txt = txt[:-1]
    return txt + ".."


DEFAULT_HOLD = 5          # frames; only for regions that run a model


def reading_key(value):
    """What counts as "the same reading", ignoring what jitters every frame.

    A detect region publishing "items" hands back the class, the confidence and
    the box. The confidence moves every frame and the box moves a pixel or two,
    so comparing whole values means no two frames are ever equal - which
    silently defeated stable_frames altogether. At stable_frames 2 nothing was
    ever published at all; at 1 everything was, thirty times a second.

    What a person means by "it has not changed" is the class, or the number, or
    the text. That is what this compares.
    """
    if isinstance(value, (list, tuple)):
        names = []
        for v in value:
            n = (v.get("class") or v.get("name")) if isinstance(v, dict) else v
            names.append(str(n))
        return tuple(sorted(names))
    if isinstance(value, dict):
        return tuple(sorted((k, str(v)) for k, v in value.items()))
    return value


def is_blank(value):
    """A region that read nothing, in any of the shapes that can mean that."""
    return value is None or value == "" or value == [] or value == ()


def value_text(roi, val):
    """What a reading should look like on a screen, which is not what it is.

    A region set to publish "items" hands back a list of dicts - the class, the
    confidence and the box for every detection. Printed straight onto the frame
    and cut to fit, that becomes "[{'class': 'Gr", which reads as a row of
    broken brackets and tells nobody anything. An empty one becomes "[]", which
    looks like a fault rather than an empty slot.

    So the overlay says what a person needs - the names - while the published
    value, the log and the hook keep every field exactly as they were. This is
    a display, not a transformation.
    """
    if val is None or val == "":
        return "-"
    if isinstance(val, dict):
        return count_text(roi, val)        # report:"all" hands back the whole dict
    if isinstance(val, (list, tuple)):
        if not val:
            return "-"                     # nothing in this region right now
        names = []
        for v in val:
            n = v.get("class") or v.get("name") if isinstance(v, dict) else v
            if n is None:
                n = str(v)
            if n not in names:
                names.append(str(n))
        return ", ".join(names)
    return str(val)


def draw_overlay(frame, cam, results, fps, groups=None, show_panel=True, tracks=None,
                 period=""):
    """Boxes carry only their reading; names and confidences live in the panel.

    With one ROI per digit the boxes sit shoulder to shoulder, so a name label
    on each one overlaps its neighbours and nothing can be read. The value goes
    inside the box, an index number sits in the corner, and the panel spells out
    index, name, value and confidence.
    """
    h, w = frame.shape[:2]
    rois = [r for r in cam["rois"] if r.get("enabled", True)]

    for i, roi in enumerate(rois, 1):
        res = results.get(roi["id"], {})
        val = res.get("value")
        readable = (val not in (None, "", False) and val != []
                    and not (isinstance(val, str) and "?" in val))
        base = TASK_COLOUR.get(roi["task"], TESR_GOLD)
        colour = base if readable else TESR_RED
        shape = roi.get("shape", "rect")

        if shape == "line" and roi.get("points"):
            a, b = [(int(px * w), int(py * h)) for px, py in roi["points"][:2]]
            cv2.line(frame, a, b, colour, 2, cv2.LINE_AA)
            for e in (a, b):
                cv2.circle(frame, e, 4, colour, -1, cv2.LINE_AA)
            if roi["task"] == "line_count":
                # the arrow shows which way counts as "in"; what you see is counted
                mx, my = (a[0] + b[0]) // 2, (a[1] + b[1]) // 2
                nx, ny = LINE.gate_normal(a, b) if LINE else (0, 0)
                if (roi.get("params") or {}).get("invert"):
                    nx, ny = -nx, -ny
                cv2.arrowedLine(frame, (mx, my), (int(mx + nx * 34), int(my + ny * 34)),
                                TESR_GOLD, 2, cv2.LINE_AA, tipLength=0.35)
            pts = np.array([a, b], np.int32)
        elif shape == "polygon" and roi.get("points"):
            pts = np.array([[int(px * w), int(py * h)] for px, py in roi["points"]], np.int32)
            overlay = frame.copy()
            cv2.fillPoly(overlay, [pts], colour)
            cv2.addWeighted(overlay, 0.16, frame, 0.84, 0, frame)
            cv2.polylines(frame, [pts], True, colour, 2, cv2.LINE_AA)
        else:
            pts = roi_corners(roi["rect"], w, h).astype(int)
            cv2.polylines(frame, [pts], True, colour, 2, cv2.LINE_AA)

        x0, y0 = pts[:, 0].min(), pts[:, 1].min()
        x1, y1 = pts[:, 0].max(), pts[:, 1].max()
        bw, bh = x1 - x0, y1 - y0

        # index tag in the corner, always small enough to fit
        _panel(frame, x0, y0, 18, 14, 0.85)
        _text(frame, str(i), (x0 + 4, y0 + 11), 0.36, TESR_GOLD)

        # the reading itself, inside the box so neighbours never collide
        s = value_text(roi, val)
        if shape == "line":
            cut = 34 if isinstance(val, (dict, list, tuple)) else 14
            (tw, th), _ = cv2.getTextSize(s[:cut], cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            tx = min(w - tw - 6, max(4, int(pts[:, 0].mean()) + 12))
            ty = max(th + 30, int(pts[:, 1].min()) + th + 6)
            _panel(frame, tx - 5, ty - th - 5, tw + 10, th + 10, 0.7)
            _text(frame, s[:cut], (tx, ty), 0.6, WHITE if readable else TESR_RED, 2)
        elif len(s) <= 4 and bw > 16 and bh > 20:
            scale = max(0.5, min(1.6, bh / 45.0))
            (tw, th), _ = cv2.getTextSize(s, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
            tx, ty = x0 + (bw - tw) // 2, y1 - 4
            _panel(frame, tx - 4, ty - th - 4, tw + 8, th + 8, 0.6)
            _text(frame, s, (tx, ty), scale, WHITE if readable else TESR_RED, 2)

    for tid, tr in (tracks or {}).items():
        if tr.get("hits", 0) < 2:
            continue
        x1, y1, x2, y2 = [int(v) for v in tr["box"]]
        cv2.rectangle(frame, (x1, y1), (x2, y2), TESR_GOLD_DEEP, 1, cv2.LINE_AA)
        _text(frame, "#%d %s" % (tid, tr.get("class", "")), (x1, max(10, y1 - 4)), 0.35, TESR_GOLD)

    cv2.rectangle(frame, (0, 0), (w, 26), INK, -1)
    cv2.line(frame, (0, 26), (w, 26), TESR_GOLD_DEEP, 1)
    draw_mark(frame, 14, 13, 8)
    _text(frame, "TESR", (28, 18), 0.52, TESR_RED, 2)
    bar = "VISION RUNNER v%s | %s | %.1f FPS | q=quit s=snapshot p=panel" % (
        STUDIO_VERSION, cam.get("name", cam["id"]), fps)
    _text(frame, bar, (76, 18), 0.45, WHITE)

    if show_panel:
        strip = results_strip(w, rois, results, groups or [], period)
        frame = np.vstack([frame, strip])
    return frame


def results_strip(width, rois, results, groups, period=""):
    """Every reading in one place, drawn on its own strip that is appended under
    the frame. Adding space is safer than drawing on top: with ten ROIs spread
    over the picture there is no free corner that is guaranteed to stay empty."""
    line_h, pad, gap = 18, 12, 10
    SC_IDX, SC_NAME, SC_VAL, SC_CONF = 0.36, 0.36, 0.44, 0.32

    # Measure first, lay the columns out second. They used to sit at fixed
    # offsets - the value at x+120 and the confidence at x+165 - while the
    # value was allowed 22 characters. Anything longer than about five ran
    # straight through its own score, so "Green_Apple" overwrote the number
    # that said how sure it was. A column should be as wide as what is in it.
    cells = []
    for idx, roi in enumerate(rois):
        res = results.get(roi["id"], {})
        val = res.get("value")
        readable = (val not in (None, "", False) and val != []
                    and not (isinstance(val, str) and "?" in val))
        cells.append(("%d" % (idx + 1), roi["name"], value_text(roi, val),
                      "%.2f" % res.get("conf", 0.0), readable))

    def _widest(i, scale, cap):
        return min(cap, max([_text_w(c[i], scale) for c in cells] or [0]))

    w_idx = _widest(0, SC_IDX, 24)
    w_name = _widest(1, SC_NAME, 140)
    w_val = _widest(2, SC_VAL, 230)
    w_conf = _widest(3, SC_CONF, 40)
    col_w = w_idx + w_name + w_val + w_conf + 4 * gap

    ncol = max(1, min(4, (width - 2 * pad) // max(1, col_w)))
    rows = max(1, (len(rois) + ncol - 1) // ncol)
    ghead = 34 * len(groups)
    height = 26 + ghead + rows * line_h + pad

    strip = np.full((height, width, 3), 16, np.uint8)
    cv2.line(strip, (0, 0), (width, 0), TESR_GOLD_DEEP, 1)

    y = 18
    _text(strip, "RESULTS", (pad, y), 0.42, TESR_GOLD_DEEP)

    for name, members in groups:
        text, ok, conf = group_value(members, results)
        y += 32
        _text(strip, name, (pad, y), 0.45, GREY)
        _text(strip, text if text else "-", (pad + 78, y + 4), 0.95,
              TESR_GOLD if ok else TESR_RED, 2)
        (tw, _), _ = cv2.getTextSize(text or "-", cv2.FONT_HERSHEY_SIMPLEX, 0.95, 2)
        _text(strip, "conf %.2f" % conf, (pad + 88 + tw, y + 2), 0.38, GREY)

    y += 26
    for i, (num, name, sval, sconf, readable) in enumerate(cells):
        c, r = i // rows, i % rows
        x = pad + c * col_w
        yy = y + r * line_h
        x_name = x + w_idx + gap
        x_val = x_name + w_name + gap
        x_conf = x_val + w_val + gap
        _text(strip, num.rjust(2), (x, yy), SC_IDX, TESR_GOLD_DEEP)
        _text(strip, _fit_text(name, w_name, SC_NAME), (x_name, yy), SC_NAME, GREY)
        _text(strip, _fit_text(sval, w_val, SC_VAL), (x_val, yy), SC_VAL,
              WHITE if readable else TESR_RED)
        _text(strip, sconf, (x_conf, yy), SC_CONF, GREY)
    if period:
        _text(strip, "period  " + str(period), (width - 240, 18), 0.4, TESR_GOLD_DEEP)
    return strip


def debug_strip(debugs, width):
    """Stack the per-ROI processed images into one panel (field tuning helper)."""
    tiles = []
    for name, img in debugs:
        if img is None or img.size == 0:
            continue
        t = img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        scale = 90.0 / max(1, t.shape[0])
        t = cv2.resize(t, (max(1, int(t.shape[1] * scale)), 90))
        cv2.putText(t, name[:14], (3, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.35, TESR_GOLD, 1, cv2.LINE_AA)
        tiles.append(t)
    if not tiles:
        return None
    strip = np.hstack(tiles)
    if strip.shape[1] > width:
        s = float(width) / strip.shape[1]
        strip = cv2.resize(strip, (width, max(1, int(strip.shape[0] * s))))
    return strip


# --------------------------------------------------------------------------
# main loop
# --------------------------------------------------------------------------

def run(cfg, args):
    cam = pick_camera(cfg, args.camera_id)
    source = args.source or args.image or cam.get("source", "0")
    rt = cfg["runtime"]
    show = (rt.get("show_window", True) or args.test) and not args.headless
    if show and not gui_available():
        log("no window available here - either this OpenCV has no GUI "
            "(opencv-python-headless) or there is no display attached, which "
            "is what an SSH session looks like. Running without a window; "
            "readings still go to the console and to every output you "
            "configured. Pass --headless to mean this on purpose.", "WARN")
        show = False
    console = args.console or rt.get("console", "summary")

    rois = [r for r in cam.get("rois", []) if r.get("enabled", True)]
    if not rois:
        log("no enabled ROI for camera '%s'" % cam.get("id"), "WARN")

    auto_group = args.auto_group or bool(rt.get("auto_group"))
    groups = build_groups(rois, auto=auto_group)

    log("version  : runner v%s" % STUDIO_VERSION)
    log("platform : %s %s | python %s | opencv %s"
        % (platform.system(), platform.machine(), platform.python_version(), cv2.__version__))
    log("camera   : %s (%s)  source=%r" % (cam.get("name"), cam.get("id"), source))
    log("rois     : %s" % ", ".join("%s[%s]" % (r["name"], r["task"]) for r in rois))
    if groups:
        log("groups   : %s" % ", ".join(
            "%s(%s)" % (n, "+".join(r["name"] for r in ms)) for n, ms in groups))
    elif not auto_group:
        maybe = build_groups(rois, auto=True)
        if maybe:
            log("tip      : %d single-digit ROIs look like %d reading(s). "
                "Add --auto-group to join them, or set \"group\" on each ROI in the config."
                % (sum(len(ms) for _, ms in maybe), len(maybe)))
    log("console  : %s" % console)

    frame_rois = [r for r in rois if r.get("task") in FRAME_TASKS]
    if frame_rois and LINE is None:
        raise SystemExit("[FATAL] this config uses line/zone tasks but "
                         "tesr_line_engine.py is not next to the runner")
    needs_tracks = any(r.get("task") in ("line_count", "zone_count") for r in rois)
    detector = cam.get("detector") or {}
    if needs_tracks and not detector.get("path"):
        log("line/zone counting needs a camera detector; add "
            '"detector": {"path": "best.onnx", "class_names": [...]} to the camera', "WARN")

    tracker = LINE.Tracker(**(cam.get("tracker") or {})) if (needs_tracks and LINE) else None
    reset_cfg = rt.get("counter_reset") or {}
    ctx = {"scale": cam.get("scale"), "counters": {}, "tracks": {}, "dets": [],
           "reset": reset_cfg}

    # Counters are built up front rather than on the first crossing, so a saved
    # shift can be restored before the first frame is read.
    if LINE is not None:
        for r in rois:
            if r.get("task") == "line_count":
                ctx["counters"][r["id"]] = LINE.LineCounter(r, reset_cfg)

    state_path = counter_state_path(rt, cam.get("id", "cam"))
    if ctx["counters"]:
        any_counter = next(iter(ctx["counters"].values()))
        log("counters : reset=%s  period=%s%s"
            % (any_counter.clock.mode, any_counter.period or "-",
               ("  state=" + state_path) if state_path else ""))
        if state_path and not args.reset_counters:
            saved = load_counter_state(state_path)
            for rid, c in ctx["counters"].items():
                if c.restore(saved.get(rid)):
                    log("resumed  : %s = %s" % (c.roi["name"], c.value()))

    src = FrameSource(source, cam.get("width", 0), cam.get("height", 0),
                      kind=args.camera)
    bus = OutputBus(rt, cfg.get("project", "tesr"), cam.get("id", "cam"))

    stable = {r["id"]: {"cand": None, "count": 0, "last": None,
                        "value": None, "conf": 0.0} for r in rois}
    gstable = {n: None for n, _ in groups}
    results = {}
    show_panel = not args.no_panel
    period = 1.0 / max(1.0, float(rt.get("loop_fps", 10)))
    frame_i, fps, t_prev = 0, 0.0, time.time()
    last_summary, last_summary_at, last_state_save = None, 0.0, time.time()
    every = float(args.every or 0)

    def summary_text():
        """Every current reading on one line: groups first, then each ROI."""
        bits = []
        for name, members in groups:
            text, ok, _ = group_value(members, results)
            bits.append("%s=%s" % (name, text if text else "-"))
        for r in rois:
            v = results.get(r["id"], {}).get("value")
            bits.append("%s=%s" % (r["name"], "-" if v in (None, "") else v))
        return "  ".join(bits)

    try:
        while RUNNING:
            t0 = time.time()
            ok, frame = src.read()
            if not ok or frame is None:
                log("frame grab failed - retrying", "WARN")
                time.sleep(0.5)
                continue

            if cam.get("rotate"):
                code = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
                        270: cv2.ROTATE_90_COUNTERCLOCKWISE}.get(int(cam["rotate"]))
                if code is not None:
                    frame = cv2.rotate(frame, code)

            frame_i += 1

            # one detection pass per frame, shared by every counting shape
            if tracker is not None:
                ctx["dets"] = detect_frame(frame, detector)
                ctx["tracks"] = tracker.update(ctx["dets"], frame.shape[1], frame.shape[0])

            debugs = []
            for roi in rois:
                if frame_i % max(1, int(roi.get("every_n_frames", 1))) != 0:
                    continue
                fn = TASKS.get(roi["task"])
                if fn is None:
                    continue
                try:
                    if roi["task"] in FRAME_TASKS:
                        value, conf, dbg = fn(frame, roi, ctx)
                    else:
                        crop = crop_roi(frame, roi["rect"], upscale_to=roi.get("upscale_to", 0))
                        value, conf, dbg = fn(crop, roi)
                except Exception as exc:
                    log("ROI '%s' failed: %s" % (roi["name"], exc), "WARN")
                    continue
                if isinstance(dbg, np.ndarray):
                    debugs.append((roi["name"], dbg))

                st = stable[roi["id"]]
                need = max(1, int(roi.get("stable_frames", 1)))
                params = roi.get("params") or {}
                hold = params.get("hold")
                if hold is None:
                    hold = DEFAULT_HOLD if roi["task"] in MODEL_TASKS else 0
                hold = max(0, int(hold))

                rkey = reading_key(value)
                if rkey == st["cand"]:
                    st["count"] += 1
                else:
                    st["cand"], st["count"] = rkey, 1

                # Losing a detection is weaker evidence than gaining one. A
                # detector that missed a single frame has not told you the slot
                # is empty, so a drop to nothing has to argue for longer than a
                # change from one class to another. This is the whole of the
                # flicker: the model is right most frames and silent on some.
                need_now = need
                if hold and is_blank(value) and not is_blank(st["value"]):
                    need_now = max(need, hold)

                if st["count"] >= need_now and rkey != st["last"]:
                    st["last"], st["value"], st["conf"] = rkey, value, conf
                    key = roi.get("output_key") or roi["name"]
                    bus.publish(roi, key, value, conf, changed=True,
                                echo=(console == "change"))
                    if rt.get("save_on_change"):
                        ensure_dir(os.path.join(rt.get("save_dir", "captures"), "x"))
                        fn_ = os.path.join(rt.get("save_dir", "captures"),
                                           "%s_%s_%s.jpg" % (cam.get("id"), roi["name"],
                                                             datetime.now().strftime("%Y%m%d_%H%M%S")))
                        cv2.imwrite(fn_, frame)

                # The window and the summary console show the SETTLED reading,
                # so what you watch is what the rest of the system was told.
                # A live confidence is still shown while the class holds.
                results[roi["id"]] = {
                    "value": st["value"],
                    "conf": conf if rkey == st["last"] else st["conf"]}

            # shift / daily rollover: publish the closing numbers before zeroing
            for c in ctx["counters"].values():
                closed = c.roll()
                if closed is not None:
                    bus.publish_event("period_end", closed, echo=(console != "none"))
            if state_path and (time.time() - last_state_save) >= 5.0:
                if any(c.dirty for c in ctx["counters"].values()):
                    save_counter_state(state_path, ctx["counters"])
                    for c in ctx["counters"].values():
                        c.dirty = False
                last_state_save = time.time()

            # a completed group reading is published as its own value
            for name, members in groups:
                text, whole, gconf = group_value(members, results)
                if whole and text != gstable[name]:
                    gstable[name] = text
                    bus.publish({"id": "grp_" + name, "name": name}, name, text, gconf,
                                changed=True, echo=(console == "change"))

            if console == "summary":
                line = summary_text()
                due = every > 0 and (time.time() - last_summary_at) >= every
                if (every <= 0 and line != last_summary) or due:
                    last_summary, last_summary_at = line, time.time()
                    print("%s  %s" % (now_iso(), line), flush=True)

            dt = time.time() - t_prev
            t_prev = time.time()
            fps = 0.9 * fps + 0.1 * (1.0 / dt) if dt > 0 else fps

            if show:
                period_label = next(iter(ctx["counters"].values())).period if ctx["counters"] else ""
                view = draw_overlay(frame.copy(), cam, results, fps, groups, show_panel,
                                    ctx.get("tracks"), period_label)
                cv2.imshow("TESR Vision Runner", view)
                if args.test:
                    strip = debug_strip(debugs, view.shape[1])
                    if strip is not None:
                        cv2.imshow("ROI debug", strip)
                k = cv2.waitKey(1) & 0xFF
                if k in (ord("q"), 27):
                    break
                if k == ord("p"):
                    show_panel = not show_panel
                if k == ord("s"):
                    ensure_dir("captures/x")
                    p = "captures/snapshot_%s.jpg" % datetime.now().strftime("%Y%m%d_%H%M%S")
                    cv2.imwrite(p, view)
                    log("saved %s" % p)

            if args.once:
                summary = {r["name"]: results.get(r["id"], {}).get("value") for r in rois}
                for name, members in groups:
                    summary[name] = group_value(members, results)[0]
                print(json.dumps(summary, ensure_ascii=False, indent=2))
                break

            sleep = period - (time.time() - t0)
            if sleep > 0:
                time.sleep(sleep)
    finally:
        if state_path:
            save_counter_state(state_path, ctx["counters"])
        src.release()
        bus.close()
        close_windows()
        log("stopped cleanly")


# --------------------------------------------------------------------------
# calibrate mode : live trackbars, prints the values to paste back in the JSON
# --------------------------------------------------------------------------

def calibrate(cfg, args):
    cam = pick_camera(cfg, args.camera_id)
    roi = None
    for r in cam.get("rois", []):
        if args.calibrate in (r.get("id"), r.get("name")):
            roi = r
    if roi is None:
        raise SystemExit("[FATAL] ROI '%s' not found" % args.calibrate)

    src = FrameSource(args.source or args.image or cam.get("source", "0"),
                      cam.get("width", 0), cam.get("height", 0), kind=args.camera)
    win = "calibrate: %s" % roi["name"]
    if not gui_available():
        raise SystemExit(
            "[FATAL] --calibrate needs a window and this OpenCV build has none.\n"
            "        opencv-python-headless is installed. Either calibrate on a\n"
            "        machine with a display, or tune the values in TESR ROI Studio\n"
            "        in a browser and export the config again.")
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    p = roi.setdefault("params", {})
    cv2.createTrackbar("threshold", win, int(p.get("threshold", 128)), 255, lambda v: None)
    cv2.createTrackbar("min_fill%", win, int(float(p.get("min_fill", 0.45)) * 100), 100, lambda v: None)
    cv2.createTrackbar("blur", win, int(p.get("blur", 3)), 15, lambda v: None)
    cv2.createTrackbar("invert", win, 1 if p.get("invert", True) else 0, 1, lambda v: None)
    cv2.createTrackbar("mode 0otsu 1fix 2adap", win,
                       {"otsu": 0, "fixed": 1, "adaptive": 2}.get(p.get("threshold_mode", "otsu"), 0),
                       2, lambda v: None)
    log("adjust the sliders until every digit is clean, then press q")
    try:
        while RUNNING:
            ok, frame = src.read()
            if not ok:
                break
            p["threshold"] = cv2.getTrackbarPos("threshold", win)
            p["min_fill"] = cv2.getTrackbarPos("min_fill%", win) / 100.0
            b = cv2.getTrackbarPos("blur", win)
            p["blur"] = b if b % 2 == 1 else b + 1
            p["invert"] = bool(cv2.getTrackbarPos("invert", win))
            p["threshold_mode"] = ["otsu", "fixed", "adaptive"][cv2.getTrackbarPos("mode 0otsu 1fix 2adap", win)]
            crop = crop_roi(frame, roi["rect"], upscale_to=roi.get("upscale_to", 0))
            fn = TASKS.get(roi["task"], task_digits)
            try:
                value, conf, dbg = fn(crop, roi)
            except Exception as exc:
                value, conf, dbg = "ERR: %s" % exc, 0.0, crop
            panel = np.vstack([
                cv2.resize(crop, (480, 160)),
                cv2.resize(dbg if dbg.ndim == 3 else cv2.cvtColor(dbg, cv2.COLOR_GRAY2BGR), (480, 160))])
            cv2.putText(panel, "value: %s (%.2f)" % (value, conf), (8, 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, TESR_GOLD, 2, cv2.LINE_AA)
            cv2.imshow(win, panel)
            if (cv2.waitKey(30) & 0xFF) in (ord("q"), 27):
                break
    finally:
        src.release()
        close_windows()
    print("\nparams tuned for ROI '%s' - paste this back into your config:" % roi["name"])
    print(json.dumps(p, indent=2))


# --------------------------------------------------------------------------
# self test : renders a fake 7-segment display and reads it back
# --------------------------------------------------------------------------

def render_7seg(text, w=0, h=200):
    w = w or int(105 * (len(text) + 1))
    img = np.full((h, w, 3), 18, np.uint8)
    on = (60, 255, 120)
    off = (30, 45, 32)
    pat = {c: k for k, c in SEG_TABLE.items() if c and c != "-"}
    n = len(text)
    cw = w // (n + 1)
    for i, ch in enumerate(text):
        segs = pat.get(ch, (0,) * 7)
        x0 = int(cw * 0.5 + i * cw)
        y0 = int(h * 0.15)
        dw, dh = int(cw * 0.62), int(h * 0.7)
        t = max(4, dw // 7)
        boxes = [
            (x0 + t, y0, dw - 2 * t, t),
            (x0, y0 + t, t, dh // 2 - t),
            (x0 + dw - t, y0 + t, t, dh // 2 - t),
            (x0 + t, y0 + dh // 2 - t // 2, dw - 2 * t, t),
            (x0, y0 + dh // 2, t, dh // 2 - t),
            (x0 + dw - t, y0 + dh // 2, t, dh // 2 - t),
            (x0 + t, y0 + dh - t, dw - 2 * t, t),
        ]
        for s, (bx, by, bw_, bh_) in zip(segs, boxes):
            cv2.rectangle(img, (bx, by), (bx + bw_, by + bh_), on if s else off, -1)
    return img


def selftest():
    log("self test - rendering a synthetic 7-segment display")
    ok_all = True
    roi = {"id": "t", "name": "test", "task": "digits_7seg",
           "rect": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "angle": 0},
           "params": {"threshold_mode": "otsu", "invert": False, "blur": 3,
                      "min_fill": 0.45, "split": "auto", "as_number": False}}
    for sample in ["0123456789", "4207", "1234", "8888", "9051"]:
        img = render_7seg(sample)
        value, conf, _ = task_digits(img, roi)
        good = str(value) == sample
        ok_all = ok_all and good
        log("expect %-11s got %-11s conf %.2f  %s"
            % (sample, value, conf, "OK" if good else "MISMATCH"),
            "INFO" if good else "WARN")
    log("self test %s" % ("PASSED" if ok_all else "FAILED - tune params on real images"),
        "INFO" if ok_all else "WARN")
    return 0 if ok_all else 1


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="TESR Edge AI Vision Runner v%s" % STUDIO_VERSION)
    ap.add_argument("--config", default="config.json", help="config.json from TESR ROI Studio")
    ap.add_argument("--camera", type=camera_kind, metavar="{usb,csi,ip,file}",
                    help="camera type, same words as predict.py in chapters 5 and 6: "
                         "usb webcam, csi ribbon camera, ip stream, file on disk")
    ap.add_argument("--camera-id", dest="camera_id", metavar="NAME",
                    help="pick a camera out of the config by id or name (default: the first)")
    ap.add_argument("--source", help="where it comes from: an index for usb/csi (0, 1, 2), "
                                     "a URL for ip, a path for file")
    ap.add_argument("--image", help="run on a single image file")
    ap.add_argument("--once", action="store_true", help="process one frame, print JSON, exit")
    ap.add_argument("--test", action="store_true", help="show preview window + ROI debug panel")
    ap.add_argument("--headless", action="store_true", help="no window (service / SSH / Jetson)")
    ap.add_argument("--console", choices=["summary", "change", "none"],
                    help="summary: one line with every reading (default) · "
                         "change: one line per ROI as it changes · none: quiet")
    ap.add_argument("--every", type=float, default=0,
                    help="with --console summary, print every N seconds instead of on change")
    ap.add_argument("--auto-group", dest="auto_group", action="store_true",
                    help="join single-digit ROIs that sit on the same row into one reading")
    ap.add_argument("--no-panel", dest="no_panel", action="store_true",
                    help="hide the on-screen results panel")
    ap.add_argument("--reset-counters", dest="reset_counters", action="store_true",
                    help="start the shift counters at zero, ignoring the saved state file")
    ap.add_argument("--calibrate", help="tune params of one ROI with live sliders")
    ap.add_argument("--list", action="store_true", help="list cameras and ROIs, then exit")
    ap.add_argument("--selftest", action="store_true", help="verify the digit reader offline")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args()

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)

    if args.version:
        print("TESR Vision Runner %s (schema %s)" % (STUDIO_VERSION, SCHEMA))
        return 0
    if args.selftest:
        return selftest()

    cfg = load_config(args.config)

    if args.list:
        print("project: %s" % cfg.get("project"))
        for c in cfg.get("cameras", []):
            print(" camera %s (%s) source=%s" % (c.get("id"), c.get("name"), c.get("source")))
            for r in c.get("rois", []):
                print("   - %-16s %-14s key=%s enabled=%s"
                      % (r.get("name"), r.get("task"), r.get("output_key"), r.get("enabled", True)))
            gs = build_groups([r for r in c.get("rois", []) if r.get("enabled", True)],
                              auto=args.auto_group)
            for name, members in gs:
                print("   = group %-10s <- %s" % (name, " ".join(m["name"] for m in members)))
        return 0

    if args.calibrate:
        calibrate(cfg, args)
        return 0

    run(cfg, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
