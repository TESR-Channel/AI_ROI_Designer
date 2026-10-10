#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End to end through the runner's own loop: a synthetic video of a box crossing
a gate, a shift boundary crossed while it runs, and a restart that has to pick
the shift back up from the state file.

No camera, no model: the detector is replaced with a function that returns the
box positions directly, which is the only part of the chain this test is not
trying to exercise.
"""

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "runner"))
import tesr_vision_runner as R           # noqa: E402
import tesr_line_engine as LINE          # noqa: E402

FAIL = []


def check(name, got, want):
    ok = got == want
    print("%-56s %s   got=%r" % (name, "ok " if ok else "FAIL", got))
    if not ok:
        FAIL.append("%s: got %r want %r" % (name, got, want))


WORK = tempfile.mkdtemp(prefix="tesr_counter_")
VIDEO = os.path.join(WORK, "gate.mp4")
STATE = os.path.join(WORK, "counters_{camera}.json")
W, H, N = 320, 240, 24


def make_video():
    """One box walking left to right across the middle of the frame."""
    vw = cv2.VideoWriter(VIDEO, cv2.VideoWriter_fourcc(*"mp4v"), 10, (W, H))
    for i in range(N):
        f = np.full((H, W, 3), 30, np.uint8)
        x = int(20 + (W - 80) * i / float(N - 1))
        cv2.rectangle(f, (x, 90), (x + 40, 150), (220, 220, 220), -1)
        vw.write(f)
    vw.release()
    return os.path.isfile(VIDEO)


def box_detector(frame, det):
    """Stand in for the ONNX detector: find the one bright blob in the frame."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY)
    cnts, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if w * h < 200:
            continue
        out.append({"box": [float(x), float(y), float(x + w), float(y + h)],
                    "class": "box", "conf": 0.9})
    return out


def config(reset_mode):
    return {
        "schema": "tesr.roi.v1",
        "project": "counter loop test",
        "runtime": {"loop_fps": 1000, "show_window": False, "print_console": False,
                    "console": "none",
                    "counter_reset": dict(reset_mode, state_file=STATE)},
        "cameras": [{
            "id": "cam1", "name": "Gate", "source": VIDEO,
            "width": W, "height": H,
            "detector": {"path": "stub.onnx", "class_names": ["box"]},
            "rois": [{
                "id": "gate", "name": "gate", "enabled": True, "task": "line_count",
                "shape": "line", "output_key": "gate", "stable_frames": 1,
                "points": [[0.5, 0.1], [0.5, 0.9]],
                "params": {"label_in": "in", "label_out": "out", "report": "all"},
            }],
        }],
    }


class Args:
    camera = source = image = calibrate = None
    once = test = headless = list = selftest = version = False
    auto_group = no_panel = reset_counters = False
    console = "none"
    every = 0


def run(cfg, reset_counters=False):
    """Run the real loop to the end of the video and hand back the counters."""
    path = os.path.join(WORK, "cfg.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    loaded = R.load_config(path)
    args = Args()
    args.reset_counters = reset_counters
    R.detect_frame = box_detector

    # FrameSource rewinds a video file on purpose so a short clip can be looped
    # while tuning; here the loop has to stop after exactly one pass
    orig_read = R.FrameSource.read
    seen = {"n": 0}

    def read(self):
        seen["n"] += 1
        if seen["n"] > N:
            R.RUNNING = False
            return False, None
        return orig_read(self)

    R.FrameSource.read = read
    R.RUNNING = True
    try:
        R.run(loaded, args)
    finally:
        R.FrameSource.read = orig_read
    return loaded


print("--- a box crosses the gate, counted through the real loop ---")
check("test video written", make_video(), True)

cfg = config({"mode": "shift", "shifts": ["06:00", "14:00", "22:00"]})
run(cfg)
state_file = STATE.replace("{camera}", "cam1")
check("state file written", os.path.isfile(state_file), True)
saved = json.load(open(state_file, encoding="utf-8"))
check("one crossing counted in, none out",
      (saved["gate"]["counts"]["in"], saved["gate"]["counts"]["out"]), (1, 0))
check("lifetime recorded too", saved["gate"]["lifetime"]["in"], 1)
check("the saved period is a shift label",
      len(saved["gate"]["period"].split()) == 2, True)

print()
print("--- restart inside the same shift continues the count ---")
run(cfg)
saved2 = json.load(open(state_file, encoding="utf-8"))
check("second pass adds to the same shift", saved2["gate"]["counts"]["in"], 2)
check("lifetime adds up", saved2["gate"]["lifetime"]["in"], 2)

print()
print("--- restart with --reset-counters starts the shift at zero ---")
run(cfg, reset_counters=True)
saved3 = json.load(open(state_file, encoding="utf-8"))
check("shift count restarted", saved3["gate"]["counts"]["in"], 1)
check("lifetime also restarted when the state is ignored",
      saved3["gate"]["lifetime"]["in"], 1)

print()
print("--- a saved shift that has since ended is not carried forward ---")
old = json.load(open(state_file, encoding="utf-8"))
old["gate"]["period"] = "1999-01-01 A"
old["gate"]["counts"] = {"in": 500, "out": 7}
old["gate"]["lifetime"] = {"in": 900, "out": 9}
with open(state_file, "w", encoding="utf-8") as f:
    json.dump(old, f)
run(cfg)
saved4 = json.load(open(state_file, encoding="utf-8"))
check("the old shift's count is dropped", saved4["gate"]["counts"]["in"], 1)
check("but the lifetime total is kept", saved4["gate"]["lifetime"]["in"], 901)

print()
print("--- a rollover during the run publishes the closing numbers ---")
os.remove(state_file)
log_path = os.path.join(WORK, "events.jsonl")
cfg_roll = config({"mode": "shift", "shifts": ["06:00", "14:00", "22:00"]})
cfg_roll["runtime"]["jsonl_log"] = log_path

# Rather than wait for a real shift boundary, make the clock move on once the
# box has crossed. Everything after that - detecting the change, publishing the
# closing record, zeroing the counts - is the runner's own code.
real_boundary = LINE.PeriodClock.boundary
calls = {"n": 0}


def moving_clock(self, now=None):
    calls["n"] += 1
    start, label = real_boundary(self, now)
    if calls["n"] > 18:                      # after the crossing, before the end
        return start + timedelta(hours=8), label.replace(" A", " B")
    return start, label


LINE.PeriodClock.boundary = moving_clock
try:
    run(cfg_roll)
finally:
    LINE.PeriodClock.boundary = real_boundary

events = [json.loads(l) for l in open(log_path, encoding="utf-8") if l.strip()]
ends = [e for e in events if e.get("event") == "period_end"]
check("exactly one period_end was published", len(ends), 1)
check("it carries the closing count", ends[0]["in"] if ends else None, 1)
check("it names the ROI", ends[0].get("roi") if ends else None, "gate")
check("it names the period that closed",
      bool(ends and ends[0].get("period")) and ends[0]["period"].endswith(" A"), True)
check("it timestamps both ends of the period",
      bool(ends and ends[0].get("period_start") and ends[0].get("period_end")), True)
check("the new period starts at zero",
      json.load(open(state_file, encoding="utf-8"))["gate"]["counts"]["in"], 0)
check("the lifetime total is not zeroed",
      json.load(open(state_file, encoding="utf-8"))["gate"]["lifetime"]["in"], 1)

print()
print("--- reset off behaves exactly as before the feature ---")
os.remove(state_file)
cfg_off = config({"mode": "none"})
cfg_off["runtime"]["counter_reset"]["state_file"] = ""
run(cfg_off)
check("no state file is written when reset is off",
      os.path.isfile(state_file), False)

shutil.rmtree(WORK, ignore_errors=True)
print()
if FAIL:
    print("%d FAILED" % len(FAIL))
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("all counter loop checks passed")
