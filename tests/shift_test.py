#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shift / daily counter reset: does the clock roll over where it should, and
do the counts survive a restart without carrying an old shift forward?"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "runner"))
import tesr_line_engine as LINE          # noqa: E402

FAIL = []


def check(name, got, want):
    ok = got == want
    print("%-58s %s   got=%r" % (name, "ok " if ok else "FAIL", got))
    if not ok:
        FAIL.append("%s: got %r want %r" % (name, got, want))


def dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- the clock
shifts = {"mode": "shift", "shifts": ["06:00", "14:00", "22:00"]}
c = LINE.PeriodClock(shifts)
check("06:00 start of shift A", c.boundary(dt("2026-10-10 06:00"))[1], "2026-10-10 A")
check("13:59 still shift A", c.boundary(dt("2026-10-10 13:59"))[1], "2026-10-10 A")
check("14:00 becomes shift B", c.boundary(dt("2026-10-10 14:00"))[1], "2026-10-10 B")
check("22:30 becomes shift C", c.boundary(dt("2026-10-10 22:30"))[1], "2026-10-10 C")
# the night shift keeps the date it started on, the way a handover sheet reads
check("01:00 next day is still C of the 10th",
      c.boundary(dt("2026-10-11 01:00"))[1], "2026-10-10 C")
check("05:59 next day is still C of the 10th",
      c.boundary(dt("2026-10-11 05:59"))[1], "2026-10-10 C")
check("06:00 next day is A of the 11th",
      c.boundary(dt("2026-10-11 06:00"))[1], "2026-10-11 A")

named = LINE.PeriodClock({"mode": "shift",
                          "shifts": [{"at": "08:00", "name": "day"},
                                     {"at": "20:00", "name": "night"}]})
check("named shifts use the given name",
      named.boundary(dt("2026-10-10 09:00"))[1], "2026-10-10 day")

daily = LINE.PeriodClock({"mode": "daily", "at": "07:00"})
check("daily before the reset hour belongs to yesterday",
      daily.boundary(dt("2026-10-10 06:30"))[1], "2026-10-09")
check("daily after the reset hour is today",
      daily.boundary(dt("2026-10-10 07:30"))[1], "2026-10-10")

hourly = LINE.PeriodClock({"mode": "hourly"})
check("hourly label", hourly.boundary(dt("2026-10-10 15:40"))[1], "2026-10-10 15h")

mins = LINE.PeriodClock({"mode": "minutes", "minutes": 30})
check("30-minute bucket floors to the half hour",
      mins.boundary(dt("2026-10-10 15:44"))[1], "2026-10-10 15:30")

check("mode none never rolls", LINE.PeriodClock({}).boundary(dt("2026-10-10 15:44"))[1], "")
check("shift mode with no shifts falls back to daily",
      LINE.PeriodClock({"mode": "shift", "shifts": []}).mode, "daily")
check("junk time is ignored, not crashed on",
      LINE.PeriodClock({"mode": "shift", "shifts": ["nonsense", "08:00"]}).shifts,
      [(480, "A")])


# ------------------------------------------------------- counting and rolling
roi = {"id": "gate", "name": "door", "shape": "line",
       "points": [[0.5, 0.1], [0.5, 0.9]],
       "params": {"reset": shifts}}


def walk(counter, xs, w=640, h=480, tid=1):
    """Drive one track across the gate along the given x positions."""
    tracks = {}
    for x in xs:
        px = x * w
        box = [px - 20, h * 0.4, px + 20, h * 0.6]
        c = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
        if tid in tracks:
            tracks[tid]["prev"] = tracks[tid]["centroid"]
            tracks[tid]["centroid"], tracks[tid]["box"] = c, box
            tracks[tid]["hits"] += 1
        else:
            tracks[tid] = {"box": box, "centroid": c, "prev": c, "hits": 1,
                           "age": 0, "class": "Orange"}
        counter.update(tracks, w, h)


lc = LINE.LineCounter(roi)
lc.period_start, lc.period = lc.clock.boundary(dt("2026-10-10 13:50"))
walk(lc, [0.2, 0.35, 0.65, 0.8], tid=1)
walk(lc, [0.2, 0.35, 0.65, 0.8], tid=2)
walk(lc, [0.8, 0.65, 0.35, 0.2], tid=3)
v = lc.value()
check("two in, one out counted", (v["in"], v["out"], v["net"]), (2, 1, 1))
check("period reported with the value", v["period"], "2026-10-10 A")
check("lifetime kept alongside", (v["lifetime_in"], v["lifetime_out"]), (2, 1))

closed = lc.roll(dt("2026-10-10 14:00"))
check("rollover returns the closing numbers",
      (closed["period"], closed["in"], closed["out"]), ("2026-10-10 A", 2, 1))
check("rollover record carries the ROI name", closed["roi"], "door")
check("counts are zero in the new shift",
      (lc.value()["in"], lc.value()["out"]), (0, 0))
check("new shift label", lc.period, "2026-10-10 B")
check("lifetime survives the rollover",
      (lc.value()["lifetime_in"], lc.value()["lifetime_out"]), (2, 1))
check("rolling again inside the same shift does nothing",
      lc.roll(dt("2026-10-10 15:00")), None)

walk(lc, [0.2, 0.35, 0.65, 0.8], tid=9)
check("counting continues in the new shift", lc.value()["in"], 1)
check("lifetime adds up across shifts", lc.value()["lifetime_in"], 3)

# a counter with reset off must behave exactly as it did before this feature
plain = LINE.LineCounter({"id": "g", "name": "g", "points": [[0.5, 0.1], [0.5, 0.9]],
                          "params": {}})
walk(plain, [0.2, 0.35, 0.65, 0.8], tid=1)
check("reset off: no rollover", plain.roll(dt("2030-01-01 00:00")), None)
check("reset off: no period keys (only by_class, which says what crossed)",
      sorted(plain.value().keys()), ["by_class", "in", "net", "out", "total"])
check("by_class counts the crossing in, by class",
      plain.value()["by_class"], {"Orange": 1})
fresh = LINE.LineCounter({"id": "f", "name": "f", "points": [[0.5, 0.1], [0.5, 0.9]], "params": {}})
check("nothing crossed: the original keys only",
      sorted(fresh.value().keys()), ["in", "net", "out", "total"])


# --------------------------------------------------------- restart behaviour
saved = lc.state()

same = LINE.LineCounter(roi)
same.period_start, same.period = same.clock.boundary(dt("2026-10-10 15:30"))
check("restart inside the same shift resumes it", same.restore(saved), True)
check("resumed shift count", same.value()["in"], 1)
check("resumed lifetime", same.value()["lifetime_in"], 3)

later = LINE.LineCounter(roi)
later.period_start, later.period = later.clock.boundary(dt("2026-10-11 09:00"))
check("restart in a later shift does not resume it", later.restore(saved), False)
check("later shift starts at zero", later.value()["in"], 0)
check("later shift still knows the lifetime total", later.value()["lifetime_in"], 3)

broken = LINE.LineCounter(roi)
check("a missing state file is harmless", broken.restore(None), False)
check("a corrupt state file is harmless", broken.restore({"counts": {"in": "x"}}), False)

print()
if FAIL:
    print("%d FAILED" % len(FAIL))
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("all shift/reset checks passed")
