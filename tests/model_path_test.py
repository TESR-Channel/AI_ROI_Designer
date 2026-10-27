#!/usr/bin/env python3
"""A relative model path: the working folder first, then next to the config,
then next to the runner (runner 1.8.9). Run from the repo root."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "runner"))
import tesr_vision_runner as R  # noqa: E402

fails = 0


def check(what, got, want):
    global fails
    ok = got == want
    fails += not ok
    print("%-44s %s  (got %r)" % (what, "ok" if ok else "FAIL", got))


with tempfile.TemporaryDirectory() as top:
    proj = os.path.join(top, "from_web")
    os.makedirs(proj)
    cfg = os.path.join(proj, "x_config.json")
    json.dump({"schema": R.SCHEMA, "rois": []}, open(cfg, "w"))
    open(os.path.join(proj, "model.onnx"), "wb").write(b"x")
    os.chdir(top)
    R.load_config(cfg)
    check("next to the config, run from one folder up", R.find_model("model.onnx"),
          os.path.join("from_web", "model.onnx"))
    open(os.path.join(top, "model.onnx"), "wb").write(b"y")
    check("the working folder still wins", R.find_model("model.onnx"), "model.onnx")
    check("missing everywhere -> unchanged", R.find_model("nope.onnx"), "nope.onnx")
    check("absolute path -> unchanged", R.find_model(cfg), cfg)

print("model path lookup: %s" % ("PASSED" if not fails else "%d FAILED" % fails))
sys.exit(1 if fails else 0)
