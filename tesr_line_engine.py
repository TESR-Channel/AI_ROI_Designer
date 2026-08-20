#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TESR line & zone engine
=======================
The geometry that a rectangle cannot express:

  line     -> water / tank level, workpiece dimension, a counting gate
  polygon  -> an area to count things in, or to ignore

Everything here works on a whole frame rather than a crop, because a counting
line only means something in the coordinate system of the picture it was drawn on.

Kept dependency-free beyond OpenCV and numpy so it runs on a Raspberry Pi.
"""

from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


# --------------------------------------------------------------------------
# geometry helpers - all shapes are stored normalised 0..1
# --------------------------------------------------------------------------

def denorm(points, w, h):
    return np.array([[p[0] * w, p[1] * h] for p in points], dtype=np.float32)


def line_length(points, w, h):
    p = denorm(points, w, h)
    return float(np.hypot(p[1, 0] - p[0, 0], p[1, 1] - p[0, 1]))


def sample_profile(gray, points, w, h, thickness=5):
    """Average brightness along a line, one value per pixel step.

    Averaging across a few parallel offsets makes the reading far steadier than
    a single-pixel scan: sensor noise and a scratch on the tank glass no longer
    look like a water surface.
    """
    p = denorm(points, w, h)
    (x0, y0), (x1, y1) = p[0], p[1]
    n = max(2, int(round(np.hypot(x1 - x0, y1 - y0))))
    t = np.linspace(0.0, 1.0, n)
    xs, ys = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t

    dx, dy = x1 - x0, y1 - y0
    norm = np.hypot(dx, dy) or 1.0
    nx, ny = -dy / norm, dx / norm            # unit normal to the line

    half = max(0, int(thickness) // 2)
    offsets = range(-half, half + 1) if half else [0]
    acc = np.zeros(n, np.float64)
    used = 0
    H, W = gray.shape[:2]
    for k in offsets:
        sx = np.clip((xs + nx * k).round().astype(int), 0, W - 1)
        sy = np.clip((ys + ny * k).round().astype(int), 0, H - 1)
        acc += gray[sy, sx].astype(np.float64)
        used += 1
    return acc / max(1, used), t


def smooth1d(a, k):
    if k < 3:
        return a
    k = int(k) | 1
    pad = k // 2
    padded = np.pad(a, pad, mode="edge")
    kern = np.ones(k) / k
    return np.convolve(padded, kern, mode="valid")


def point_in_polygon(pt, poly):
    """Ray casting. poly is an (N,2) array of pixel coordinates."""
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and \
           (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi):
            inside = not inside
        j = i
    return inside


def side_of_line(pt, a, b):
    """Which side of the directed line a->b the point falls on: +1, -1 or 0."""
    v = (b[0] - a[0]) * (pt[1] - a[1]) - (b[1] - a[1]) * (pt[0] - a[0])
    if v > 0:
        return 1
    if v < 0:
        return -1
    return 0


def gate_normal(a, b):
    """The direction an object must travel for the crossing to count as "in".

    Defined as the line rotated 90 degrees clockwise, so a gate drawn downwards
    counts left-to-right traffic as coming in. The editor draws this arrow on
    the line, so what you see is what gets counted - and `invert` flips it
    without having to redraw anything.
    """
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = np.hypot(dx, dy) or 1.0
    return (dy / n, -dx / n)


def side_of_gate(pt, a, b):
    """+1 when the point lies on the side the "in" arrow points towards."""
    nx, ny = gate_normal(a, b)
    v = (pt[0] - a[0]) * nx + (pt[1] - a[1]) * ny
    if v > 1e-9:
        return 1
    if v < -1e-9:
        return -1
    return 0


def segments_intersect(p1, p2, p3, p4):
    """Does segment p1p2 cross segment p3p4?"""
    d1 = side_of_line(p1, p3, p4)
    d2 = side_of_line(p2, p3, p4)
    d3 = side_of_line(p3, p1, p2)
    d4 = side_of_line(p4, p1, p2)
    return d1 != d2 and d3 != d4


# --------------------------------------------------------------------------
# scale: pixels -> millimetres
# --------------------------------------------------------------------------

def px_per_unit(scale, w, h):
    """A reference line of known real length gives the conversion factor.

    Returns (factor, unit) or (None, "") when the camera has not been calibrated.
    Without it every measurement is reported in pixels only, which is honest:
    a length in millimetres that nobody calibrated would be a made-up number.
    """
    if not scale:
        return None, ""
    ref = scale.get("ref_points")
    length = float(scale.get("ref_length") or 0)
    if not ref or len(ref) != 2 or length <= 0:
        return None, scale.get("unit", "")
    px = line_length(ref, w, h)
    if px <= 0:
        return None, scale.get("unit", "")
    return px / length, scale.get("unit", "mm")


def _edges(mag, thr):
    """Locate every edge along the profile as a fractional sample index.

    Each run of samples above the threshold is one edge. Its position is the
    centre of mass of that run, weighted by gradient strength. Blurring or
    smoothing turns a sharp edge into a plateau several samples wide; picking
    the first sample of that plateau pushes every measurement outward, and
    picking any single sample of it quantises the answer. The centre of mass
    handles both, and lands between samples where the edge actually is.

    Returns [(position, peak_magnitude, peak_index), ...] left to right.
    """
    out = []
    i, n = 0, len(mag)
    while i < n:
        if mag[i] < thr:
            i += 1
            continue
        j = i
        while j < n and mag[j] >= thr:
            j += 1
        seg = mag[i:j].astype(np.float64)
        weight = seg.sum()
        if weight > 0:
            centre = float((np.arange(i, j) * seg).sum() / weight)
        else:
            centre = float(i)
        peak = int(i + np.argmax(seg))
        out.append((centre + 0.5, float(seg.max()), peak))   # +0.5: edges sit between samples
        i = j
    return out


def _subpixel(mag, i):
    """Parabolic fit through a peak and its neighbours -> fractional index."""
    if 0 < i < len(mag) - 1:
        a, b, c = float(mag[i - 1]), float(mag[i]), float(mag[i + 1])
        denom = a - 2 * b + c
        if abs(denom) > 1e-9:
            delta = 0.5 * (a - c) / denom
            if -1.0 < delta < 1.0:
                return i + delta + 0.5
    return i + 0.5


# --------------------------------------------------------------------------
# task: level along a line  (tank, silo, sight glass)
# --------------------------------------------------------------------------

def task_level(frame, roi, ctx):
    """Find the surface along a line drawn from the EMPTY end to the FULL end.

    The surface is the strongest brightness step along that line. Reported as a
    percentage of the line, and converted to engineering units when the ROI
    carries value_at_start / value_at_end.
    """
    p = roi.get("params") or {}
    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    prof, t = sample_profile(gray, roi["points"], w, h, p.get("thickness", 7))
    prof = smooth1d(prof, int(p.get("smooth", 9)))

    grad = np.diff(prof)
    if grad.size == 0:
        return None, 0.0, {}

    edge = p.get("edge", "auto")
    if edge == "dark_to_light":
        cand = np.maximum(grad, 0)
    elif edge == "light_to_dark":
        cand = np.maximum(-grad, 0)
    else:
        cand = np.abs(grad)

    # ignore the very ends: the rim of the tank is a step too
    margin = max(1, int(len(cand) * float(p.get("ignore_ends", 0.06))))
    lo, hi = (margin, len(cand) - margin) if len(cand) > 2 * margin + 2 else (0, len(cand))
    core = cand[lo:hi]
    if core.size == 0:
        return None, 0.0, {}
    i = int(np.argmax(core)) + lo
    strength = float(cand[i])

    if strength < float(p.get("min_contrast", 6)):
        return None, 0.0, {"reason": "no clear surface", "strength": strength}

    # On a noisy or textured line every sample looks like a step. A genuine
    # surface splits the line into two roughly uniform stretches - liquid on one
    # side, air on the other - so test that model rather than trusting the peak.
    split = i + 1
    before, after = prof[:split], prof[split:]
    if before.size < 3 or after.size < 3:
        return None, 0.0, {"reason": "surface at the very end of the line"}
    step = abs(float(before.mean()) - float(after.mean()))
    spread = (float(before.std()) + float(after.std())) / 2.0
    min_c = float(p.get("min_contrast", 6))
    if step < min_c:
        return None, 0.0, {"reason": "the two sides look the same",
                           "step": round(step, 1)}
    if step < spread * float(p.get("min_step_to_noise", 1.2)):
        return None, 0.0, {"reason": "the line is too textured to trust",
                           "step": round(step, 1), "spread": round(spread, 1)}

    found = _edges(cand, max(float(p.get("min_contrast", 6)), strength * 0.5))
    pos = min(found, key=lambda e: abs(e[2] - i))[0] if found else _subpixel(cand, i)
    frac = float(np.clip(pos / max(1, len(prof) - 1), 0.0, 1.0))

    # confidence: how far the winning step stands above the rest of the line
    rest = np.delete(cand, slice(max(0, i - 2), i + 3))
    base = float(np.percentile(rest, 90)) if rest.size else 0.0
    conf = float(np.clip((strength - base) / max(4.0, strength), 0.0, 1.0))

    v0, v1 = p.get("value_at_start"), p.get("value_at_end")
    if v0 is not None and v1 is not None:
        value = float(v0) + (float(v1) - float(v0)) * frac
        value = round(value, int(p.get("decimals", 1)))
        unit = p.get("unit", "")
    else:
        value = round(frac * 100.0, int(p.get("decimals", 1)))
        unit = "%"

    return value, conf, {"frac": frac, "index": i, "strength": strength, "unit": unit}


# --------------------------------------------------------------------------
# task: dimension between two edges along a line
# --------------------------------------------------------------------------

def _marks_along(prof, min_contrast, dark=None):
    """Find printed marks along the profile and return their centres.

    Measuring between two ruler ticks or two printed lines is a different job
    from measuring a solid part: what matters is the CENTRE of each mark, not
    its edges, and the space between the marks looks exactly like the space
    outside them. So this ignores the edge-pair logic entirely.

    The threshold is taken relative to the background rather than from a
    percentile, because a line may cross only two thin marks - in that case the
    marks are a tiny fraction of the samples and every percentile still lands on
    background.
    """
    bg = float(np.median(prof))
    below = bg - float(prof.min())
    above = float(prof.max()) - bg
    if dark is None:
        dark = below >= above
    depth = below if dark else above
    if depth < min_contrast:
        return [], dark
    thr = bg - depth * 0.5 if dark else bg + depth * 0.5
    sel = prof < thr if dark else prof > thr

    # Random texture also produces "marks". A printed mark stands clear of a
    # quiet background: measured on real scales the depth is 9x the background
    # spread or better, while noise sits around 4x, so 6x separates them with
    # room on both sides.
    quiet = prof[~sel]
    if quiet.size > 3:
        spread = float(quiet.std())
        if depth < spread * float(6.0):
            return [], dark

    runs, i, n = [], 0, len(sel)
    while i < n:
        if not sel[i]:
            i += 1
            continue
        j = i
        while j < n and sel[j]:
            j += 1
        seg = np.abs(prof[i:j] - thr)
        weight = float(seg.sum())
        centre = float((np.arange(i, j) * seg).sum() / weight) if weight > 0 else (i + j - 1) / 2.0
        runs.append({"centre": centre, "width": j - i, "depth": float(seg.max())})
        i = j
    return runs, dark


def task_measure(frame, roi, ctx):
    """Distance along a line.

    mode "outer" / "inner" measure a solid object: the line must cross the part
    with background at both ends.

    mode "marks" measures between printed marks - ruler ticks, scale lines,
    scribed marks - centre to centre, which is what a person reading a ruler
    means by "from 6 to 7".
    """
    p = roi.get("params") or {}
    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    prof, t = sample_profile(gray, roi["points"], w, h, p.get("thickness", 5))
    prof = smooth1d(prof, int(p.get("smooth", 5)))
    if prof.size < 4:
        return None, 0.0, {"reason": "line too short"}

    total_px = line_length(roi["points"], w, h)
    n = max(1, len(prof) - 1)
    thr = float(p.get("min_contrast", 12))
    factor, unit = px_per_unit(ctx.get("scale"), w, h)
    dec = int(p.get("decimals", 2))

    if p.get("mode") == "marks":
        polarity = p.get("mark_polarity", "auto")
        dark = None if polarity == "auto" else (polarity == "dark")
        marks, used_dark = _marks_along(prof, thr, dark)
        marks = [m for m in marks if m["width"] >= int(p.get("min_mark_width", 1))]
        if len(marks) < 2:
            return None, 0.0, {"reason": "fewer than two marks", "marks": len(marks)}
        span_px = abs(marks[-1]["centre"] - marks[0]["centre"]) / n * total_px
        if factor:
            value = round(span_px / factor, dec)
        else:
            value, unit = round(span_px, 1), "px"
        conf = float(np.clip(min(marks[0]["depth"], marks[-1]["depth"]) / 40.0, 0.0, 1.0))
        return value, conf, {"px": span_px, "unit": unit, "marks": len(marks),
                             "dark": used_dark,
                             "centres": [m["centre"] for m in marks]}

    grad = np.diff(prof)
    if grad.size < 3:
        return None, 0.0, {}

    mag = np.abs(grad)
    edges = _edges(mag, thr)
    if len(edges) < 2:
        return None, 0.0, {"reason": "fewer than two edges", "max_grad": float(mag.max())}

    if p.get("mode", "outer") == "outer":
        e0, e1 = edges[0], edges[-1]
    else:                                        # the strongest opposing pair
        rising = [e for e in edges if grad[e[2]] > 0]
        falling = [e for e in edges if grad[e[2]] < 0]
        if not rising or not falling:
            e0, e1 = edges[0], edges[-1]
        else:
            e0 = min(rising[0], falling[0], key=lambda e: e[0])
            e1 = max(rising[-1], falling[-1], key=lambda e: e[0])

    typical = float(np.median(mag)) + 1e-6
    ratio = min(e0[1], e1[1]) / typical
    if ratio < float(p.get("min_peak_ratio", 3.0)):
        return None, 0.0, {"reason": "edges do not stand out from the texture",
                           "ratio": round(ratio, 2)}

    # the material between the two edges must differ from the background,
    # otherwise two unrelated features are being measured as if they were a part
    a, b = int(min(e0[0], e1[0])) + 1, int(max(e0[0], e1[0])) + 1
    inside = prof[a:b]
    outside = np.concatenate([prof[:a], prof[b:]])
    if inside.size and outside.size:
        step = abs(float(inside.mean()) - float(outside.mean()))
        if step < thr * 0.5:
            return None, 0.0, {"reason": "nothing solid between the edges",
                               "step": round(step, 1)}

    span_px = abs(e1[0] - e0[0]) / n * total_px

    if factor:
        value = round(span_px / factor, dec)
    else:
        value, unit = round(span_px, 1), "px"

    conf = float(np.clip(min(e0[1], e1[1]) / 40.0, 0.0, 1.0))
    return value, conf, {"px": span_px, "unit": unit,
                         "i0": round(e0[0], 2), "i1": round(e1[0], 2),
                         "edges": len(edges)}


# --------------------------------------------------------------------------
# a small tracker, enough to tell one person from the next between frames
# --------------------------------------------------------------------------

def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if inter <= 0:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


class Tracker:
    """Greedy IoU matching with a centroid fallback.

    Not a Kalman filter and not trying to be: for a doorway or a conveyor at a
    sensible frame rate this is accurate and costs almost nothing on a Pi.
    """

    def __init__(self, iou_thr=0.3, max_age=12, max_dist=0.08):
        self.iou_thr = iou_thr
        self.max_age = max_age
        self.max_dist = max_dist          # fraction of the frame diagonal
        self.tracks = {}                  # id -> {box, centroid, age, hits, trail}
        self._next = 1

    def update(self, dets, w, h):
        """dets: [{"box":[x1,y1,x2,y2], "class":name, "conf":float}] in pixels."""
        diag = float(np.hypot(w, h)) or 1.0
        for tr in self.tracks.values():
            tr["age"] += 1
            tr["matched"] = False

        order = sorted(range(len(dets)), key=lambda i: -dets[i].get("conf", 0))
        for di in order:
            d = dets[di]
            box = d["box"]
            c = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
            best, best_score = None, 0.0
            for tid, tr in self.tracks.items():
                if tr["matched"]:
                    continue
                score = iou(box, tr["box"])
                if score < self.iou_thr:
                    dist = np.hypot(c[0] - tr["centroid"][0], c[1] - tr["centroid"][1]) / diag
                    score = (1.0 - dist / self.max_dist) * self.iou_thr if dist < self.max_dist else 0.0
                if score > best_score:
                    best, best_score = tid, score
            if best is None:
                tid = self._next
                self._next += 1
                self.tracks[tid] = {"box": box, "centroid": c, "age": 0, "hits": 1,
                                    "prev": c, "class": d.get("class", ""), "matched": True}
            else:
                tr = self.tracks[best]
                tr["prev"] = tr["centroid"]
                tr["box"], tr["centroid"] = box, c
                tr["age"], tr["hits"], tr["matched"] = 0, tr["hits"] + 1, True
                tr["class"] = d.get("class", tr.get("class", ""))

        for tid in [t for t, tr in self.tracks.items() if tr["age"] > self.max_age]:
            del self.tracks[tid]
        return self.tracks


# --------------------------------------------------------------------------
# task: counting across a line, with direction
# --------------------------------------------------------------------------

class LineCounter:
    def __init__(self, roi):
        p = roi.get("params") or {}
        self.roi = roi
        self.name_in = p.get("label_in", "in")
        self.name_out = p.get("label_out", "out")
        self.classes = p.get("classes") or []
        self.invert = bool(p.get("invert"))
        self.counts = {self.name_in: 0, self.name_out: 0}
        self.seen = {}                    # track id -> last side

    def update(self, tracks, w, h):
        a, b = denorm(self.roi["points"], w, h)
        crossed = []
        alive = set()
        for tid, tr in tracks.items():
            if tr["hits"] < 2:
                continue
            if self.classes and tr.get("class") not in self.classes:
                continue
            alive.add(tid)
            side = side_of_gate(tr["centroid"], a, b)
            prev_side = self.seen.get(tid)
            self.seen[tid] = side
            if prev_side is None or side == 0 or prev_side == 0 or side == prev_side:
                continue
            # the centroid moved from one side to the other: only count it if the
            # path actually passes between the endpoints, not off the end of the line
            if not segments_intersect(tr["prev"], tr["centroid"], a, b):
                continue
            going_in = (side > 0)
            if self.invert:
                going_in = not going_in
            key = self.name_in if going_in else self.name_out
            self.counts[key] += 1
            crossed.append((tid, key))
        for tid in [t for t in self.seen if t not in tracks]:
            del self.seen[tid]
        return crossed

    def value(self):
        i, o = self.counts[self.name_in], self.counts[self.name_out]
        return {self.name_in: i, self.name_out: o, "net": i - o, "total": i + o}


# --------------------------------------------------------------------------
# task: how many things are inside a polygon right now
# --------------------------------------------------------------------------

def zone_count(roi, tracks_or_dets, w, h, use_tracks=True):
    poly = denorm(roi["points"], w, h)
    p = roi.get("params") or {}
    classes = p.get("classes") or []
    anchor = p.get("anchor", "centroid")
    n, names = 0, []
    items = tracks_or_dets.values() if use_tracks else tracks_or_dets
    for it in items:
        cls = it.get("class", "")
        if classes and cls not in classes:
            continue
        box = it["box"]
        if anchor == "bottom":
            pt = ((box[0] + box[2]) / 2.0, box[3])      # feet, for people on a floor
        else:
            pt = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
        if point_in_polygon(pt, poly):
            n += 1
            names.append(cls)
    return n, names


if __name__ == "__main__":
    print(__doc__)
