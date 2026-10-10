#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Licence plate preset: does it read a plate, and - more important - does it
refuse to answer when what it sees is not a plate?

Synthetic plates only. Real plates at a real gate have motion blur, rain, glare
and a tilt, so treat these numbers as a check that the logic works, never as an
accuracy figure for a site.
"""

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "runner"))
import tesr_vision_runner as R           # noqa: E402

FAIL = []


def check(name, got, want):
    ok = got == want
    print("%-54s %s   got=%r" % (name, "ok " if ok else "FAIL", got))
    if not ok:
        FAIL.append("%s: got %r want %r" % (name, got, want))


def plate_img(text, h=42, blur=0, noise=0, dark=False, tilt=0):
    """A plate-looking strip: characters on a flat background, nothing fancy."""
    scale = h / 42.0
    w = int(26 * scale * len(text)) + 20
    bg, fg = (30, 30, 30) if dark else (235, 235, 235), \
             (235, 235, 235) if dark else (25, 25, 25)
    img = np.full((h, w, 3), bg, np.uint8)
    cv2.putText(img, text, (10, int(h * 0.76)), cv2.FONT_HERSHEY_SIMPLEX,
                0.95 * scale, fg, max(1, int(2 * scale)), cv2.LINE_AA)
    if tilt:
        M = cv2.getRotationMatrix2D((w / 2, h / 2), tilt, 1.0)
        img = cv2.warpAffine(img, M, (w, h), borderValue=bg)
    if blur:
        img = cv2.GaussianBlur(img, (blur | 1, blur | 1), 0)
    if noise:
        img = np.clip(img.astype(np.int16) +
                      np.random.RandomState(7).randint(-noise, noise + 1, img.shape),
                      0, 255).astype(np.uint8)
    return img


def read(img, **params):
    p = {"lang": "eng", "pattern": "", "min_chars": 4}
    p.update(params)
    value, conf, _ = R.task_plate(img, {"params": p})
    return value


# ------------------------------------------------------------- it should read
print("--- plates it should read ---")
for txt in ("AB1234", "7KZ8812", "GX9021", "1MMM999"):
    check("reads %s" % txt, read(plate_img(txt)), txt)

print()
print("--- awkward but still a plate ---")
check("small plate is upscaled first", read(plate_img("AB1234", h=22)), "AB1234")
check("dark plate, light characters", read(plate_img("CD5678", dark=True)), "CD5678")
check("slight blur", read(plate_img("EF4321", blur=3)), "EF4321")
check("sensor noise", read(plate_img("GH8765", noise=18)), "GH8765")
check("slight tilt", read(plate_img("JK2468", tilt=3)), "JK2468")

# ------------------------------------------------------- it should NOT answer
print()
print("--- it must refuse rather than invent ---")
blank = np.full((40, 200, 3), 240, np.uint8)
check("blank strip reads nothing", read(blank), "")

texture = np.random.RandomState(3).randint(0, 255, (40, 200, 3), dtype=np.uint8)
check("random texture reads nothing", read(texture), "")

check("two characters is not a plate", read(plate_img("AB"), min_chars=4), "")

# --------------------------------------------------------- format enforcement
print()
print("--- the plate format is the safety net ---")
check("a reading that fits the format is kept",
      read(plate_img("AB1234"), pattern=r"^[A-Z]{2}[0-9]{4}$"), "AB1234")
check("a reading that cannot fit the format is withheld",
      read(plate_img("ABCDEF"), pattern=r"^[A-Z]{2}[0-9]{4}$"), "")

# the confusable swap is only allowed to fire when the format then matches
check("O read in a digit slot is corrected to 0",
      R.plate_clean("ABO234", {"pattern": r"^[A-Z]{2}[0-9]{4}$"})[0], "AB0234")
check("0 read in a letter slot is corrected to O",
      R.plate_clean("A01234", {"pattern": r"^[A-Z]{2}[0-9]{4}$"})[0], "AO1234")
check("no swap is invented when none makes the format fit",
      R.plate_clean("XY77", {"pattern": r"^[A-Z]{2}[0-9]{4}$", "min_chars": 4})[0], "")
check("without a pattern nothing is swapped",
      R.plate_clean("ABO234", {})[0], "ABO234")
check("characters outside the alphabet are dropped",
      R.plate_clean("*AB-1234*", {})[0], "AB1234")
check("a broken pattern does not block the read",
      R.plate_clean("AB1234", {"pattern": "([unclosed"})[0], "AB1234")
check("too many characters is refused",
      R.plate_clean("AB123456789012", {})[0], "")
check("lower case input is normalised",
      R.plate_clean("ab1234", {})[0], "AB1234")

print()
if FAIL:
    print("%d FAILED" % len(FAIL))
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("all plate checks passed")
