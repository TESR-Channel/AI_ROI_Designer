#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The analogue needle gauge task, against dials this test draws itself.

Every dial here is a drawing: a clean circle, printed ticks, a straight needle
at an angle we chose. So what is checked is the geometry - does the task
recover the angle it was given, and does it refuse when there is no needle to
find. A real gauge behind scratched glass, lit from one side and photographed
at an angle is a different picture; the tolerance below says nothing about one,
and no number here should be quoted as an accuracy figure for a site.

    python3 tests/gauge_test.py
"""

import math
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "runner"))
import tesr_vision_runner as R           # noqa: E402

FAIL = []

# The whole test is written against one scale: a dial sweeping clockwise from
# 225 degrees round to -45 degrees, reading 0 to 100.
MIN_ANGLE, MAX_ANGLE = 225.0, -45.0
MIN_VALUE, MAX_VALUE = 0.0, 100.0

# How far a recovered reading may sit from the drawn one, in units of this
# dial's full scale. The needle direction is searched to the nearest half
# degree over a 270 degree scale, and the drawn needle is a few pixels wide,
# so a correct answer is bounded by the ray spacing plus the needle width;
# 2% of full scale leaves room for both.
TOL = 2.0


def check(label, got, expected):
    ok = got == expected
    print("%-58s %s   got=%r" % (label, "ok " if ok else "FAIL", got))
    if not ok:
        FAIL.append("%s: got %r want %r" % (label, got, expected))


def near(label, got, want, tol=TOL):
    ok = got is not None and abs(got - want) <= tol
    print("%-58s %s   got=%s want=%.2f  (tol %.2f)"
          % (label, "ok " if ok else "FAIL",
             "None" if got is None else "%.2f" % got, want, tol))
    if not ok:
        FAIL.append("%s: got %s want %.2f +/- %.2f" % (label, got, want, tol))


# --------------------------------------------------------------------------
# the synthetic dial
# --------------------------------------------------------------------------

def dial(angle, size=240, length=0.78, thickness=3, tail=0.0, ticks=True,
         numbers=True, arc=False, noise=0, light=False, pad=(0, 0, 0, 0)):
    """A round gauge with a straight needle at `angle` degrees.

    0 degrees points right and the angle grows anticlockwise, which is the
    convention the task uses. `pad` is (left, top, right, bottom) in pixels and
    moves the dial off the centre of the picture, so the centre parameters get
    exercised too. `arc` paints a warning band near the rim: a long dark mark
    that is NOT joined to the hub, which is the thing the needle has to beat.
    """
    face, mark = ((28, 28, 28), (235, 235, 235)) if light \
        else ((246, 246, 246), (22, 22, 22))
    img = np.full((size, size, 3), face, np.uint8)
    c = size // 2
    rad = int(size * 0.46)
    cv2.circle(img, (c, c), rad, mark, 2, cv2.LINE_AA)
    if arc:
        cv2.ellipse(img, (c, c), (int(rad * 0.84), int(rad * 0.84)),
                    0, -70, 10, mark, max(2, int(rad * 0.10)), cv2.LINE_AA)
    if ticks:
        for k in range(11):
            a = math.radians(MIN_ANGLE + (MAX_ANGLE - MIN_ANGLE) * k / 10.0)
            cv2.line(img,
                     (int(c + math.cos(a) * rad * 0.82),
                      int(c - math.sin(a) * rad * 0.82)),
                     (int(c + math.cos(a) * rad * 0.95),
                      int(c - math.sin(a) * rad * 0.95)),
                     mark, 2, cv2.LINE_AA)
            if numbers:
                cv2.putText(img, str(k * 10),
                            (int(c + math.cos(a) * rad * 0.66) - 10,
                             int(c - math.sin(a) * rad * 0.66) + 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, mark, 1, cv2.LINE_AA)
    if length > 0:
        a = math.radians(angle)
        cv2.line(img,
                 (int(round(c - math.cos(a) * rad * tail)),
                  int(round(c + math.sin(a) * rad * tail))),
                 (int(round(c + math.cos(a) * rad * length)),
                  int(round(c - math.sin(a) * rad * length))),
                 mark, thickness, cv2.LINE_AA)
        cv2.circle(img, (c, c), max(3, thickness + 2), mark, -1, cv2.LINE_AA)

    if noise:
        rng = np.random.default_rng(11)
        grain = rng.integers(-noise, noise + 1, img.shape, dtype=np.int16)
        img = np.clip(img.astype(np.int16) + grain, 0, 255).astype(np.uint8)

    l, t, r, b = pad
    if l or t or r or b:
        big = np.full((size + t + b, size + l + r, 3), face, np.uint8)
        big[t:t + size, l:l + size] = img
        img = big
    return img


# task parameters a sweep may override; everything else is a dial drawing option
TASK_KEYS = ("needle", "pointer", "center_x", "center_y", "threshold_mode",
             "threshold", "min_length", "scan_inner", "scan_outer", "max_fill",
             "clamp", "min_angle", "max_angle", "min_value", "max_value",
             "decimals")


def params(**over):
    p = {"min_angle": MIN_ANGLE, "max_angle": MAX_ANGLE,
         "min_value": MIN_VALUE, "max_value": MAX_VALUE, "decimals": 2}
    p.update(over)
    return p


def read(img, **over):
    roi = {"id": "g", "name": "gauge", "task": "gauge_needle",
           "params": params(**over)}
    value, conf, _dbg = R.task_gauge(img, roi)
    return value, conf


def truth(angle):
    """The value a needle at `angle` points at on this dial's scale."""
    f = (angle - MIN_ANGLE) / (MAX_ANGLE - MIN_ANGLE)
    return MIN_VALUE + (MAX_VALUE - MIN_VALUE) * f


def sweep(label, n=28, **kw):
    """Walk a needle across the whole scale and report the worst reading."""
    over = {k: kw.pop(k) for k in list(kw) if k in TASK_KEYS}
    worst, at, missing = 0.0, None, []
    for i in range(n):
        angle = MIN_ANGLE + (MAX_ANGLE - MIN_ANGLE) * i / (n - 1.0)
        got, _ = read(dial(angle, **kw), **over)
        if got is None:
            missing.append(round(angle, 1))
            continue
        if abs(got - truth(angle)) > worst:
            worst, at = abs(got - truth(angle)), round(angle, 1)
    if missing:
        print("%-58s %s   no reading at %s deg" % (label, "FAIL", missing))
        FAIL.append("%s: no reading at %s" % (label, missing))
        return
    ok = worst <= TOL
    print("%-58s %s   worst=%.2f at %s deg  (tol %.2f)"
          % (label, "ok " if ok else "FAIL", worst, at, TOL))
    if not ok:
        FAIL.append("%s: worst error %.2f of full scale (tol %.2f)"
                    % (label, worst, TOL))


# --------------------------------------------------------------------------

print("gauge_needle: synthetic dials, %g -> %g degrees reading %g -> %g"
      % (MIN_ANGLE, MAX_ANGLE, MIN_VALUE, MAX_VALUE))
print("tolerance: %.1f of full scale, on drawn dials only\n" % TOL)

print("-- a needle at a known angle, one dial at a time")
for angle in (225.0, 180.0, 135.0, 90.0, 45.0, 0.0, -45.0):
    near("needle at %7.1f deg" % angle, read(dial(angle))[0], truth(angle))

print("\n-- the whole scale, once per dial style")
sweep("plain dial")
sweep("thin needle", thickness=2)
sweep("thick needle", thickness=6)
sweep("needle with a tail", tail=0.25)
sweep("short needle", length=0.52)
sweep("needle reaching the tick marks", length=0.95)
sweep("no ticks, no numbers", ticks=False, numbers=False)
sweep("warning band painted near the rim", arc=True)
sweep("sensor grain", noise=22)
sweep("small dial, 120 px", size=120)
sweep("large dial, 480 px", size=480)
sweep("light needle on a dark face", light=True, needle="light")
sweep("fixed threshold instead of otsu", threshold_mode="fixed", threshold=140)

print("\n-- a counterweighted needle, whose back end is the longer one")
# pointer=long reads the counterweight and so lands half a turn out; pointer=
# short reads the end the user actually looks at.
sweep("counterweight longer than the pointer, pointer=short",
      length=0.46, tail=0.80, pointer="short")
cw = dial(135.0, length=0.46, tail=0.80)       # back end lands at -45 deg
near("the same dial, pointer=short", read(cw, pointer="short")[0], truth(135.0))
near("the same dial, pointer=long, reads the counterweight",
     read(cw, pointer="long")[0], truth(-45.0))

print("\n-- the dial off the centre of the ROI, centre given in params")
# 60 px of slack on the left and 20 on top: the dial centre is no longer the
# ROI centre, so the task has to be told where it is.
PW, PH = 240 + 60 + 10, 240 + 20 + 30
sweep("dial padded left and top", pad=(60, 20, 10, 30),
      center_x=(60 + 120) / float(PW), center_y=(20 + 120) / float(PH))

print("\n-- the sweep direction comes from the two reference angles alone")
# 0 now sits at -45 and 100 at 225, so a needle at 135 has come two thirds of
# the way round instead of one third: the same picture, the opposite reading.
near("anticlockwise scale, needle at 135 deg",
     read(dial(135.0), min_angle=-45.0, max_angle=225.0)[0], 200.0 / 3)
near("anticlockwise scale, needle straight up",
     read(dial(90.0), min_angle=-45.0, max_angle=225.0)[0], 50.0)

print("\n-- engineering units, not percent")
near("0 to 16 bar, needle straight up",
     read(dial(90.0), min_value=0.0, max_value=16.0)[0], 8.0, tol=16.0 * TOL / 100)
near("-30 to 50 C, needle at the bottom of the scale",
     read(dial(225.0), min_value=-30.0, max_value=50.0)[0], -30.0,
     tol=80.0 * TOL / 100)

print("\n-- what it must refuse to answer")
check("a dial with no needle", read(dial(90.0, length=0.0))[0], None)
check("a blank white crop", read(np.full((240, 240, 3), 250, np.uint8))[0], None)
check("a blank black crop", read(np.full((240, 240, 3), 4, np.uint8))[0], None)
check("random noise, not a dial",
      read(np.random.default_rng(7).integers(
          0, 255, (240, 240, 3), dtype=np.uint8))[0], None)
check("a face that is mostly needle-coloured",
      read(cv2.circle(np.full((240, 240, 3), 250, np.uint8),
                      (120, 120), 100, (10, 10, 10), -1))[0], None)
check("a needle shorter than min_length",
      read(dial(90.0, length=0.30), min_length=0.60)[0], None)
check("an ROI too small to hold a dial", read(dial(90.0, size=12))[0], None)
check("the centre given in the wrong place",
      read(dial(90.0), center_x=0.15, center_y=0.15)[0], None)
check("a needle in the dead sector, clamp off",
      read(dial(270.0), clamp=False)[0], None)

print("\n-- the dead sector under the hub, with clamp on (the default)")
near("needle just past the bottom of the scale",
     read(dial(255.0))[0], MIN_VALUE, tol=0.01)
near("needle just past the top of the scale",
     read(dial(290.0))[0], MAX_VALUE, tol=0.01)

print("\n-- the reading travels the normal result path")
check("registered in TASKS", R.TASKS.get("gauge_needle") is R.task_gauge, True)
check("not a frame task", "gauge_needle" in R.FRAME_TASKS, False)
check("has an overlay colour", "gauge_needle" in R.TASK_COLOUR, True)

roi = {"id": "g", "name": "gauge", "task": "gauge_needle", "params": params()}
value, conf, dbg = R.TASKS["gauge_needle"](dial(90.0), roi)
check("returns a number", isinstance(value, float), True)
check("returns a confidence in 0..1", 0.0 <= conf <= 1.0, True)
check("returns a debug image", isinstance(dbg, np.ndarray), True)

seen = []
R.on_result = lambda rec, changed: seen.append(rec)
bus = R.OutputBus({"print_console": False}, "test", "cam1")
bus.publish(roi, "pressure", value, conf, changed=False, echo=False)
bus.close()
check("reaches on_result() as the number it read",
      bool(seen) and seen[0]["value"] == value, True)

print()
if FAIL:
    print("FAILED (%d)" % len(FAIL))
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("gauge_needle: all checks passed")
