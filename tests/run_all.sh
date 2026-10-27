#!/usr/bin/env bash
# Everything that can be checked without a camera, a model or a factory.
#
#   bash tests/run_all.sh
#
# Needs: python3 + opencv-python + numpy, tesseract (for the plate test),
#        node + jsdom (for the page test). Each test says what it skips.
set -u
cd "$(dirname "$0")/.."

pass=0
fail=0
skip=0

run() {                      # run <name> <command...>
  local name="$1"; shift
  printf '\n\033[1m=== %s ===\033[0m\n' "$name"
  if "$@"; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    printf '\033[31m%s FAILED\033[0m\n' "$name"
  fi
}

skip_msg() {
  printf '\n\033[33m=== %s SKIPPED: %s ===\033[0m\n' "$1" "$2"
  skip=$((skip + 1))
}

# the exported Python must still be byte-identical to runner/
run "embedded python in sync" python3 -I tools/embed_python.py --check

# the digit reader, offline, against rendered 7-segment displays
run "digit reader selftest" python3 -I runner/tesr_vision_runner.py --selftest

# shift and daily counter reset, and surviving a restart
run "shift / daily reset" python3 -I tests/shift_test.py

# the whole runner loop: a box crossing a gate, a rollover, a state file
run "counter loop end to end" python3 -I tests/counter_loop_test.py

# the needle gauge, against dials drawn by the test itself
run "needle gauge" python3 -I tests/gauge_test.py

run "model next to the config" python3 -I tests/model_path_test.py

# the licence plate preset, including the readings it must refuse
if command -v tesseract >/dev/null 2>&1; then
  run "licence plate preset" python3 -I tests/plate_test.py
else
  skip_msg "licence plate preset" "tesseract is not installed"
fi

# the example configs have to keep running
run "demo config" python3 -I runner/tesr_vision_runner.py \
  --config examples/demo_config.json --image examples/sample_machine.jpg \
  --once --headless
run "grouped meters config" python3 -I runner/tesr_vision_runner.py \
  --config examples/meters_grouped.json --image examples/meters.jpg \
  --once --headless
if command -v tesseract >/dev/null 2>&1; then
  run "plate config" python3 -I runner/tesr_vision_runner.py \
    --config examples/plate_config.json --image examples/plate.jpg \
    --once --headless
else
  skip_msg "plate config" "tesseract is not installed"
fi

# the page itself: boots without an error, both languages complete, plate logic
if node -e 'require("jsdom")' >/dev/null 2>&1; then
  run "index.html in a headless DOM" node tests/page_test.js
else
  skip_msg "index.html in a headless DOM" "npm install jsdom first"
fi

printf '\n\033[1m%d passed, %d failed, %d skipped\033[0m\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
