#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TESR Edge AI Vision Runner
==========================
Thai Embedded Systems and Robotics — Build, Train and Deploy AI to Edge Devices
Runs the ROI configuration exported from TESR ROI Studio (roi_designer.html).

One config file -> many cameras -> many ROIs -> one task per ROI.
Runs on Windows / macOS / Linux / Raspberry Pi / NVIDIA Jetson with the same code.

Minimum requirement : Python 3.8+, opencv-python, numpy
Optional            : ultralytics (YOLO), pytesseract or easyocr (OCR),
                      pyzbar (1D barcode), paho-mqtt (MQTT)

Usage
-----
    python tesr_vision_runner.py --config config.json --test
    python tesr_vision_runner.py --config config.json --camera cam1 --headless
    python tesr_vision_runner.py --config config.json --source rtsp://... --test
    python tesr_vision_runner.py --config config.json --image sample.jpg --once
    python tesr_vision_runner.py --config config.json --calibrate roi_1
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

VERSION = "1.0.0"
SCHEMA = "tesr.roi.v1"

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
}


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if cfg.get("schema") != SCHEMA:
        log("config schema is '%s', expected '%s' - trying anyway"
            % (cfg.get("schema"), SCHEMA), "WARN")
    rt = dict(DEFAULT_RUNTIME)
    rt.update(cfg.get("runtime") or {})
    for key in ("mqtt", "webhook"):
        merged = dict(DEFAULT_RUNTIME[key])
        merged.update(rt.get(key) or {})
        rt[key] = merged
    cfg["runtime"] = rt
    cfg.setdefault("cameras", [])
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
    """Uniform reader for webcam index, video file, image file or stream URL."""

    def __init__(self, source, width=0, height=0):
        self.raw = source
        self.kind = "camera"
        self.cap = None
        self.still = None

        src = source
        if isinstance(src, str) and src.strip().isdigit():
            src = int(src.strip())

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
            raise SystemExit(
                "[FATAL] cannot open source: %r\n"
                "        webcam  -> try 0, 1, 2 / check permission\n"
                "        rtsp    -> check url, user/password, network" % source)
        if width:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(width))
        if height:
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(height))
        if isinstance(src, str) and os.path.isfile(src):
            self.kind = "video"

    def read(self):
        if self.kind == "image":
            return True, self.still.copy()
        ok, frame = self.cap.read()
        if not ok and self.kind == "video":
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # loop the test video
            ok, frame = self.cap.read()
        return ok, frame

    def release(self):
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
    lo = np.array([int(p.get("h_min", 0)), int(p.get("s_min", 80)), int(p.get("v_min", 80))])
    hi = np.array([int(p.get("h_max", 179)), int(p.get("s_max", 255)), int(p.get("v_max", 255))])
    mask = cv2.inRange(hsv, lo, hi)
    ratio = float(np.count_nonzero(mask)) / mask.size
    ok = ratio >= float(p.get("min_ratio", 0.20))
    return (ok if p.get("output", "bool") == "bool" else round(ratio, 4)), ratio, \
        cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)


TASKS = {
    "digits_7seg": task_digits,
    "ocr_text": task_ocr,
    "yolo_detect": task_yolo_detect,
    "yolo_classify": task_yolo_classify,
    "barcode_qr": task_code,
    "presence_diff": task_presence,
    "color_check": task_color,
}


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
                self.mqtt = mqtt_client.Client()
                if mq.get("username"):
                    self.mqtt.username_pw_set(mq["username"], mq.get("password", ""))
                self.mqtt.connect(mq.get("host", "localhost"), int(mq.get("port", 1883)), 60)
                self.mqtt.loop_start()
                log("MQTT connected -> %s:%s" % (mq.get("host"), mq.get("port")))
            except Exception as exc:  # broker down should not kill the vision loop
                log("MQTT disabled (%s)" % exc, "WARN")
                self.mqtt = None

    def publish(self, roi, key, value, conf, changed):
        ts = now_iso()
        rec = {"time": ts, "project": self.project, "camera": self.cam_id,
               "roi": roi["name"], "key": key, "value": value, "conf": round(conf, 3)}
        if self.rt.get("print_console"):
            print("%s  %-14s %-10s = %s  (conf %.2f)"
                  % (ts, roi["name"], key, value, conf), flush=True)
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
# drawing
# --------------------------------------------------------------------------

# TESR corporate identity, sampled from the company logo.
# OpenCV takes BGR, so each tuple is the reverse of the hex value in the docs.
TESR_RED = (6, 0, 243)          # #F30006
TESR_GOLD = (129, 204, 230)     # #E6CC81
TESR_GOLD_DEEP = (73, 123, 146)  # #927B49
INK = (27, 27, 27)              # #1B1B1B
WHITE = (242, 242, 242)         # #F2F2F2

TASK_COLOUR = {
    "digits_7seg": TESR_GOLD,
    "ocr_text": (65, 164, 217),      # #D9A441
    "yolo_detect": TESR_RED,
    "yolo_classify": (107, 122, 255),  # #FF7A6B
    "barcode_qr": (224, 211, 127),   # #7FD3E0
    "presence_diff": (127, 201, 134),  # #86C97F
    "color_check": (216, 155, 200),  # #C89BD8
}


def draw_mark(img, x, y, r):
    """Small TESR emblem: gold ring, dark core, red centre dot."""
    cv2.circle(img, (x, y), r, TESR_GOLD_DEEP, 1, cv2.LINE_AA)
    cv2.circle(img, (x, y), max(1, r - 3), INK, -1, cv2.LINE_AA)
    cv2.circle(img, (x, y), max(1, r - 3), TESR_GOLD, 1, cv2.LINE_AA)
    cv2.circle(img, (x, y), max(1, r // 3), TESR_RED, -1, cv2.LINE_AA)


def draw_overlay(frame, cam, results, fps):
    h, w = frame.shape[:2]
    for roi in cam["rois"]:
        if not roi.get("enabled", True):
            continue
        pts = roi_corners(roi["rect"], w, h).astype(int)
        r = results.get(roi["id"], {})
        base = TASK_COLOUR.get(roi["task"], TESR_GOLD)
        colour = base if r.get("value") not in (None, "", False) else TESR_RED
        cv2.polylines(frame, [pts], True, colour, 2, cv2.LINE_AA)
        label = "%s: %s" % (roi["name"], r.get("value", "-"))
        x, y = pts[0]
        cv2.rectangle(frame, (x, max(0, y - 20)), (x + 12 * len(label), y), INK, -1)
        cv2.rectangle(frame, (x, max(0, y - 20)), (x + 3, y), colour, -1)
        cv2.putText(frame, label, (x + 7, max(12, y - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, WHITE, 1, cv2.LINE_AA)

    cv2.rectangle(frame, (0, 0), (w, 26), INK, -1)
    cv2.line(frame, (0, 26), (w, 26), TESR_GOLD_DEEP, 1)
    draw_mark(frame, 14, 13, 8)
    cv2.putText(frame, "TESR", (28, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.52, TESR_RED, 2, cv2.LINE_AA)
    bar = "VISION RUNNER v%s | %s | %.1f FPS | q=quit  s=snapshot" % (
        VERSION, cam.get("name", cam["id"]), fps)
    cv2.putText(frame, bar, (76, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, WHITE, 1, cv2.LINE_AA)
    return frame


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
    cam = pick_camera(cfg, args.camera)
    source = args.source or args.image or cam.get("source", "0")
    rt = cfg["runtime"]
    show = (rt.get("show_window", True) or args.test) and not args.headless

    rois = [r for r in cam.get("rois", []) if r.get("enabled", True)]
    if not rois:
        log("no enabled ROI for camera '%s'" % cam.get("id"), "WARN")

    log("platform : %s %s | python %s | opencv %s"
        % (platform.system(), platform.machine(), platform.python_version(), cv2.__version__))
    log("camera   : %s (%s)  source=%r" % (cam.get("name"), cam.get("id"), source))
    log("rois     : %s" % ", ".join("%s[%s]" % (r["name"], r["task"]) for r in rois))

    src = FrameSource(source, cam.get("width", 0), cam.get("height", 0))
    bus = OutputBus(rt, cfg.get("project", "tesr"), cam.get("id", "cam"))

    stable = {r["id"]: {"cand": None, "count": 0, "last": None} for r in rois}
    results = {}
    period = 1.0 / max(1.0, float(rt.get("loop_fps", 10)))
    frame_i, fps, t_prev = 0, 0.0, time.time()

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
            debugs = []
            for roi in rois:
                if frame_i % max(1, int(roi.get("every_n_frames", 1))) != 0:
                    continue
                fn = TASKS.get(roi["task"])
                if fn is None:
                    continue
                crop = crop_roi(frame, roi["rect"], upscale_to=roi.get("upscale_to", 0))
                try:
                    value, conf, dbg = fn(crop, roi)
                except Exception as exc:
                    log("ROI '%s' failed: %s" % (roi["name"], exc), "WARN")
                    continue
                results[roi["id"]] = {"value": value, "conf": conf}
                debugs.append((roi["name"], dbg))

                st = stable[roi["id"]]
                need = max(1, int(roi.get("stable_frames", 1)))
                if value == st["cand"]:
                    st["count"] += 1
                else:
                    st["cand"], st["count"] = value, 1
                if st["count"] == need and value != st["last"]:
                    st["last"] = value
                    key = roi.get("output_key") or roi["name"]
                    bus.publish(roi, key, value, conf, changed=True)
                    if rt.get("save_on_change"):
                        ensure_dir(os.path.join(rt.get("save_dir", "captures"), "x"))
                        fn_ = os.path.join(rt.get("save_dir", "captures"),
                                           "%s_%s_%s.jpg" % (cam.get("id"), roi["name"],
                                                             datetime.now().strftime("%Y%m%d_%H%M%S")))
                        cv2.imwrite(fn_, frame)

            dt = time.time() - t_prev
            t_prev = time.time()
            fps = 0.9 * fps + 0.1 * (1.0 / dt) if dt > 0 else fps

            if show:
                view = draw_overlay(frame.copy(), cam, results, fps)
                cv2.imshow("TESR Vision Runner", view)
                if args.test:
                    strip = debug_strip(debugs, view.shape[1])
                    if strip is not None:
                        cv2.imshow("ROI debug", strip)
                k = cv2.waitKey(1) & 0xFF
                if k in (ord("q"), 27):
                    break
                if k == ord("s"):
                    ensure_dir("captures/x")
                    p = "captures/snapshot_%s.jpg" % datetime.now().strftime("%Y%m%d_%H%M%S")
                    cv2.imwrite(p, view)
                    log("saved %s" % p)

            if args.once:
                summary = {r["name"]: results.get(r["id"], {}).get("value") for r in rois}
                print(json.dumps(summary, ensure_ascii=False, indent=2))
                break

            sleep = period - (time.time() - t0)
            if sleep > 0:
                time.sleep(sleep)
    finally:
        src.release()
        bus.close()
        cv2.destroyAllWindows()
        log("stopped cleanly")


# --------------------------------------------------------------------------
# calibrate mode : live trackbars, prints the values to paste back in the JSON
# --------------------------------------------------------------------------

def calibrate(cfg, args):
    cam = pick_camera(cfg, args.camera)
    roi = None
    for r in cam.get("rois", []):
        if args.calibrate in (r.get("id"), r.get("name")):
            roi = r
    if roi is None:
        raise SystemExit("[FATAL] ROI '%s' not found" % args.calibrate)

    src = FrameSource(args.source or args.image or cam.get("source", "0"),
                      cam.get("width", 0), cam.get("height", 0))
    win = "calibrate: %s" % roi["name"]
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
        cv2.destroyAllWindows()
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
    ap = argparse.ArgumentParser(description="TESR Edge AI Vision Runner v%s" % VERSION)
    ap.add_argument("--config", default="config.json", help="config.json from TESR ROI Studio")
    ap.add_argument("--camera", help="camera id or name in the config")
    ap.add_argument("--source", help="override source (0, rtsp://..., video.mp4)")
    ap.add_argument("--image", help="run on a single image file")
    ap.add_argument("--once", action="store_true", help="process one frame, print JSON, exit")
    ap.add_argument("--test", action="store_true", help="show preview window + ROI debug panel")
    ap.add_argument("--headless", action="store_true", help="no window (service / SSH / Jetson)")
    ap.add_argument("--calibrate", help="tune params of one ROI with live sliders")
    ap.add_argument("--list", action="store_true", help="list cameras and ROIs, then exit")
    ap.add_argument("--selftest", action="store_true", help="verify the digit reader offline")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args()

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)

    if args.version:
        print("TESR Vision Runner %s (schema %s)" % (VERSION, SCHEMA))
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
        return 0

    if args.calibrate:
        calibrate(cfg, args)
        return 0

    run(cfg, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
