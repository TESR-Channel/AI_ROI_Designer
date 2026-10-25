# TESR ROI Studio + Vision Runner

*ฉบับภาษาไทย: [README.th.md](README.th.md) · [Back to the repository README](README.md)*

**Open the tool: https://tesr-channel.github.io/AI_ROI_Designer/**

Two pieces that work together for AI Vision on edge devices.

| File | What it does | Where it runs |
|---|---|---|
| `index.html` | Web tool: **draw multiple ROIs per camera**, pick a task or a model per ROI, read values right in the browser, then export config + Python | Any browser, no install, no network needed |
| `tesr_line_engine.py` | The line and zone engine: level, dimension, line counting, zone counting | Sits beside the runner |
| `tesr_vision_runner.py` | Reads the config and processes the real camera, video or image, publishing to CSV / JSONL / MQTT / Webhook | Windows, macOS, Linux, Raspberry Pi, Jetson, mini PC |

The idea: **draw the ROIs once, get a config file, run it anywhere and get the same result.**

The interface ships in **English and Thai**. Use the TH / EN switch in the header; the choice is remembered on that machine. Exported configs and Python are identical in either language.

---

## 0. Opening it

Two ways, same file.

**Double-click `index.html`.** Nothing to install, no server.

**Or use the published copy at https://tesr-channel.github.io/AI_ROI_Designer/.** Better for sharing with a team or a customer, and the live camera always works because the page is served over https. Section 1.4 covers publishing your own.

Python only enters the picture once you export and run on the target device.

The only thing that needs the internet is the **first use of an `.onnx` model or OCR**, because the engine is fetched then. Drawing ROIs, reading digits, colour checks, change detection, QR and barcodes all work fully offline.

---

## 1. Workflow

```text
Photograph the machine
  -> open index.html and drag out ROIs (many per camera, many cameras)
  -> pick a task per ROI (digits / OCR / YOLO / QR & barcode / change / colour)
  -> read values in the browser to tune the parameters
  -> export config.json + tesr_vision_runner.py
  -> try it on a still image (--once)
  -> test on site with a live preview (--test) and sliders (--calibrate)
  -> run headless (--headless) and feed the values into MQTT / Node-RED / a database / a dashboard
```

The page has three working modes.

| Mode | When to use it |
|---|---|
| **Still image** | Draw ROIs on a photo from the site. Most precise, because nothing moves. |
| **Live camera** | See the real camera in the middle of the screen with values attached to each ROI in real time. Use it while aiming the camera. |
| **Freeze frame** | While live, press once when the angle looks right; the frame becomes the reference image for careful ROI work. |

---

## 1.4 Publishing on GitHub Pages

The live copy of this repository is at

**https://tesr-channel.github.io/AI_ROI_Designer/**

Everything needed to publish your own is already in the repository root:

```
index.html                 the tool, one self-contained file
.nojekyll                  keeps Jekyll from touching the files
.github/workflows/ci.yml   runs the test suite on every push
```

1. Fork this repository, or create one and upload these files
2. **Settings → Pages → Source**, choose **Deploy from a branch** → `main` / root
3. Push to `main` and wait about a minute

`index.html` is at the repository root so Pages can serve it with no build step: the
page you push is the page that is served.

The workflow does not deploy. It runs `tests/run_all.sh` and
`tools/embed_python.py --check`, which is the check that matters — a page whose
embedded Python has drifted from `runner/` would hand visitors an export that cannot
run their own config.

Exporting config and Python still works exactly the same, because those files are generated in the visitor's browser and never touch the server.

---

## 1.5 Using a model you trained

Press **+ Add a trained model** in the left panel, choose an `.onnx` file, type the class names, then assign that model to any YOLO task ROI. Press **Test now** and the detection boxes appear immediately; in live mode it keeps running.

Export from Ultralytics like this:

```bash
yolo export model=best.pt format=onnx opset=12
```

* The model stays in that browser on that machine. Nothing is uploaded anywhere.
* **Export config + Python** puts the `.onnx` file into the project `.zip` under its deploy filename, with the config, the runner and the class names — there is nothing to copy by hand. Unzip it into one folder and run from that folder: the model path in the config is relative, and the runner opens it from the folder you run the command in, not from the runner's or the config's folder.
* On the Python side, `.onnx` runs on `onnxruntime` alone — **no Ultralytics, no PyTorch** — which matters a great deal on a Raspberry Pi.

```bash
pip install onnxruntime          # about 50 MB, versus 2 GB+ for ultralytics + torch
```

Preprocessing (letterbox) and decoding (NMS) are written to match on both sides and were tested against each other to confirm they produce the same boxes and confidences. What you see in the browser is what you get on the device.

---

## 2. Tasks available per ROI

| task | Purpose | Model needed? |
|---|---|---|
| `digits_7seg` | Read digits from a 7-segment / LED / LCD machine panel | No — pure OpenCV |
| `ocr_text` | Read general text and numbers | Python: pytesseract or easyocr · Browser: Tesseract.js (first run needs internet) |
| `plate_ocr` | Read a licence plate, with the plate format enforced | Same OCR engines as `ocr_text` |
| `yolo_detect` | Detect objects inside the ROI, count them or return details | Your own `.onnx` |
| `yolo_classify` | Classify the ROI, e.g. OK / NG | Your own `.onnx` |
| `barcode_qr` | Read QR codes and linear barcodes | No — decoders are bundled on both sides |
| `presence_diff` | Detect a change or the presence of something | No |
| `color_check` | Check a status lamp, a tower light or part colour | No |
| `gauge_needle` | Read an analogue needle gauge — pressure, temperature, flow | No — pure OpenCV |

Each ROI has its own `output_key`, so one frame can publish several values at once.

---

## 3. Installing the runner

```bash
python -m pip install --upgrade pip
python -m pip install opencv-python numpy
```

Add only what your ROIs actually need (see `requirements.txt`):

```bash
pip install onnxruntime     # if any ROI uses an .onnx model
pip install pytesseract     # if any ROI uses OCR (also install the Tesseract engine)
pip install paho-mqtt       # to publish to MQTT / Node-RED
```

Platform notes:

* **Raspberry Pi** — `sudo apt install python3-opencv` is much faster than building from pip
* **Jetson (JetPack)** — OpenCV ships with JetPack; do not install `opencv-python` over it
* **macOS** — run from a normal Terminal for the preview window, and allow Camera under System Settings → Privacy
* **Windows** — DirectShow is selected automatically when the camera is slow to open

---

## 4. Try it immediately with the sample files

```bash
python tesr_vision_runner.py --selftest                       # verify the digit reader
python tesr_vision_runner.py --config demo_config.json --once # read the sample image
```

Expected output from `sample_machine.jpg`:

```json
{
  "counter": 420.7,
  "lot_qr": "TESR-LINE-01",
  "alarm_lamp": true,
  "conveyor": false
}
```

Other sample configs:

```bash
python tesr_vision_runner.py --config ../examples/meters_grouped.json --once --auto-group
python tesr_vision_runner.py --config ../examples/plate_config.json --once
python tesr_vision_runner.py --config ../examples/linezone_config.json --list
```

### Running the checks

```bash
bash tests/run_all.sh
```

What it covers, and what it deliberately does not:

| Check | What it proves |
|---|---|
| `tools/embed_python.py --check` | the Python the export button hands out is identical to `runner/` |
| `--selftest` | the 7-segment reader, against rendered displays |
| `tests/shift_test.py` | shift and daily boundaries, including a night shift crossing midnight, and resuming after a restart |
| `tests/counter_loop_test.py` | the whole runner loop: a box crossing a gate, a rollover published as an event, the state file |
| `tests/plate_test.py` | the plate reader, and the readings it must refuse |
| `tests/page_test.js` | `index.html` boots without an error, both languages are complete, every task has a label, a hint and defaults |
| the example configs | they still run end to end |

**It does not measure accuracy.** Every image in these tests is drawn by the test itself, with
even lighting and no lens. They prove the logic is right, not that a camera on your wall will
read a plate or a meter correctly. That is what the site visit is for.

---

## 5. Commands you will actually use

```bash
# list the cameras and ROIs in a config
python tesr_vision_runner.py --config config.json --list

# test on site: live preview plus the thresholded view of every ROI  (q=quit, s=snapshot)
python tesr_vision_runner.py --config config.json --test

# tune on site with sliders, then paste the JSON back into the web tool
python tesr_vision_runner.py --config config.json --calibrate counter

# switch camera without editing the file
python tesr_vision_runner.py --config config.json --source rtsp://user:pass@192.168.1.50:554/stream1 --test

# run for real on a Jetson or Pi with no window
python tesr_vision_runner.py --config config.json --headless
```

---

## 6. Where the values go

* the console
* `logs/results.csv` — timestamp, camera, roi, key, value, conf
* `logs/results.jsonl` — one event per line, easy to load into a database
* MQTT — topic `<prefix>/<camera_id>/<output_key>`, JSON payload, ready for Node-RED
* Webhook — HTTP POST when a value changes (uses the standard library, nothing to install)
* a snapshot image when a value changes, if enabled in Runtime

A value is only published once it has read the same result `stable_frames` times in a row, which stops it flickering while a display is mid-change.

---

## 6.1 Adding your own code

You do not rewrite the runner. Settings — cameras, ROIs, thresholds, MQTT, logging — all live in `config.json`, and the web tool writes that file for you. What the runner cannot know is what *your* system wants done with a reading: publish it somewhere of your own, pull a relay, write a row to your database, apply a business rule.

That goes in the two hooks at the top of `tesr_vision_runner.py`, under the `YOUR CODE GOES HERE` banner:

* `on_result(rec, changed)` — called once for every reading that gets published. `changed` is `True` only when the value differs from the last one, so use it when you want to act on a transition rather than on every frame.
* `on_event(rec)` — called for events rather than values, such as a shift or daily counter rolling over.

`rec` is the same dictionary that goes to the CSV, the JSONL file and MQTT: `time`, `project`, `camera`, `roi`, `key`, `value`, `conf`.

```python
def on_result(rec, changed):
    if changed and rec["key"] == "line_count" and rec["value"] > 100:
        print("target reached:", rec["value"])
```

Both hooks run last, after the console, the log files, MQTT and the webhook, and each one is wrapped in its own `try/except`. If your code raises, you get a `[WARN]` line and the vision loop keeps running — so an experiment in here cannot take the station down. Leave them empty and the runner behaves exactly as it did before.

---

## 6.3 QR and barcode reading

Works out of the box on both sides. **Nothing to install, no internet required.**

| | In the browser | In Python |
|---|---|---|
| QR | BarcodeDetector if the browser has it, otherwise the bundled decoder | OpenCV QRCodeDetector |
| Code 128 · Code 39 · ITF | The bundled decoder | A decoder written into the runner |
| EAN-13 · UPC-A · EAN-8 | The bundled decoder | OpenCV first, then the runner's decoder |

`pyzbar` used to be required on the Python side (which also meant installing `libzbar0`). It no longer is. If a machine happens to have it, it is still used as a last fallback.

**Getting a good read**

* Frame the whole barcode **including the white quiet zones** on both sides — roughly ten times the narrowest bar width. Cropping the quiet zone away is the most common reason a barcode will not read.
* Keep the bars upright. About 8° of tilt is fine; beyond that use "Rotate before processing".
* Inverted labels (white bars on black) read fine with no extra setting.
* Every format is checksum-validated and needs at least two scan lines to agree before it answers. The result is a system that **fails to read** far more often than it **reads wrong**, which is the safer behaviour on a factory floor.

---

## 6.4 The "Test now" button

It sits in the **Try it in the browser** panel on the right and works for every task.

| task | What runs in the browser | Internet needed? |
|---|---|---|
| 7-segment digits | JavaScript ported directly from the Python | No — re-reads instantly on every change |
| Colour check / change detection | JavaScript | No |
| Barcode / QR | The decoders bundled into the HTML file | No — any browser |
| OCR text | Tesseract.js | First run only |
| YOLO detect / classify | onnxruntime-web | First run only |

If an engine cannot be fetched, you get a clear message within 20 seconds rather than an indefinite wait.

---

## 6.5 When a reading is unreliable, press "Auto-tune"

Select a digits ROI and look at the **Accuracy check** panel on the right.

**Auto-tune** sweeps every plausible combination (threshold method, polarity, blur, min_fill) and picks the best. What makes it different from an ordinary sweep is that it does not choose the setting that happens to read correctly once — it chooses the one that **keeps reading the same value across the widest range**, because a reading that is only just correct will flip the moment the light shifts.

The **band** figure is the width of that safe range.

| band | Meaning | What to do |
|---|---|---|
| ≥ 0.16 | Wide, copes well with lighting changes | Good to use; stable_frames of 2 is enough |
| 0.08 – 0.16 | Workable | Set stable_frames to 3 or more and retest at a different time of day |
| < 0.08 | Narrow, the value may flip | Do not put it into production yet; fix the setup first |
| failed | This image cannot be read at all | Only lighting, angle or distance will help; no software setting will |

**A new ROI defaults to 1 digit**, because one ROI per digit copes best with uneven lighting and camera angle — each digit gets its own parameters, so a digit sitting in a glare patch can be tuned without disturbing the others. To read a whole reading in one box, set "Digits in this ROI" to the real count, e.g. 4. If the count is wrong, the tool tells you how many digits it actually sees.

**Diagnose only** lists the causes with a fix for each: ROI too small, overexposed, low contrast, or a digit count that does not match.

**The order that actually works, most effective first**

1. **Light** — glare on the display glass is the number one cause. Tilt the camera 10–20° off the reflection, add a shade hood.
2. **Size** — digits should be at least 40 px tall in the ROI. Move the camera closer rather than upscaling afterwards.
3. **Camera exposure** — if the image is blown out, the data is gone for good and no software setting recovers it.
4. **Framing** — cover only the digits with a small margin; do not take in the bezel or other symbols.
5. **Set the digit count correctly** — it lets the tool reject splits that came out wrong.
6. **Then press Auto-tune.**

---

## 6.6 Seeing every ROI at once, and joining digits into one reading

Splitting a display into one ROI per digit gives you a lot of ROIs. The console used to print
one line per ROI as each value changed, which interleaves badly and is hard to follow.
The default is now **one line carrying every reading**.

```
2026-07-29T00:50:19  meter_top=225466  meter_bottom=6987  digits_1=2  digits_2=2  digits_3=5 ...
```

| Flag | Behaviour |
|---|---|
| `--console summary` | Default. One line with everything, printed when anything changes |
| `--console summary --every 1` | Print once a second regardless, good for watching on site |
| `--console change` | The old behaviour: one line per ROI as it changes |
| `--console none` | Quiet, for running as a service |

### Joining single-digit ROIs into one number

Two ways.

**From the web tool** — type the same name into the **Group** field on each ROI, e.g. `meter_top`.
ROIs sharing a group name are concatenated into one value, **ordered left to right automatically**,
so the order you happened to draw them in does not matter.

**Or let the runner work it out** — pass `--auto-group` and it clusters digit ROIs that sit on the
same row, naming them `row1`, `row2` from the top down.

```bash
python tesr_vision_runner.py --config config.json --auto-group --test
```

If the runner spots ROIs that look joinable but grouping is off, it says so at startup.

Group values are published through every channel like any other value — console, CSV, JSONL,
MQTT and Webhook — using the group name as the key.

**Important**: if any digit is unreadable the group shows as `2?5466` and **is not published**
until every digit reads. A number missing a digit is a wrong number, not a partially useful one.

### The display window

Name labels used to overlap and become unreadable once ROIs sat side by side. Now:

* **The reading is drawn inside its own box**, so it cannot spill onto a neighbour
* Each box carries a small index number in the corner
* **The results panel sits below the image rather than on top of it**, so it can never cover the
  machine display. It shows each joined reading large, then every ROI with its value and confidence
* A joined reading is gold when every digit read and red when one did not — obvious at a glance
* Press `p` to hide or show the panel

---

## 6.7 Lines and zones — counting people and vehicles, measuring level and size

A rectangle answers "read the value here". It cannot answer "how many people walked
through this doorway". So there are two more shapes.

| Shape | Task | For | Model needed? |
|---|---|---|---|
| Line | `level_line` | Water level in a tank, silo or sight glass | No |
| Line | `measure_line` | Part width, gap, any dimension | No |
| Line | `line_count` | People in and out, vehicles, parts on a conveyor, with direction | Yes |
| Zone | `zone_count` | How many things are inside an area right now | Yes |

### Level

Draw the line from **A = empty** to **B = full**. The surface is the clearest brightness step
along that line. Set `Value at A` and `Value at B` to get engineering units — 0–1000 litres
rather than a percentage.

Tested against synthetic tanks with an exactly known level: worst error **0.2%** of the line.

### Dimension

Draw the line right across the part with background at both ends. The distance between the
first and last edge is measured.

For millimetres rather than pixels, set the **camera scale** first: press "Draw reference line",
drag across something of known size, then type its real length.

Tested against parts of exactly known width: **0.26 px** error, and after calibration
**0.07 mm on a 30 mm part**.

> Stated plainly for customers: those figures come from synthetic images where everything is
> controlled. Real installations have perspective, lens distortion, and parts that do not sit in
> the same plane as the reference line. Real accuracy can only be established by measuring a
> known standard part on site, and the camera should be as close to perpendicular to the
> measured plane as the installation allows.

### Measuring a dimension, step by step

**1. Set the camera scale first** (once per camera installation)

Put something of exactly known size in the same plane as the part — a tape measure or a gauge.
Press **Draw reference line**, drag along a known distance (say the 0 to 10 cm marks),
then type `100` as the real length and `mm` as the unit.

A longer reference is a better one: spanning 10 cm is far more accurate than spanning 1 cm.

**2. Draw the measurement line right across the part**

The mistake almost everyone makes: **both ends of the line must land on background, not on the part.**

```
right:  ────┤████████████├────     both ends off the part
wrong:      ├──████████──┤         line too short, ends sit on the part
```

The tool finds the first and last edge along the line and measures between them.
If an end sits on the part, there is no edge there to find.

**3. Read the panel on the right**

The upper strip is the actual pixels the line passes over, straightened out.
The graph below is brightness along the line, and **the two red marks are the edges it found.**

If a red mark lands in the wrong place you can see it immediately rather than guessing.

---

### Measuring between marks, for example 6 cm to 7 cm

**Outermost edges** is built for a solid part: it finds the first and last edge and measures between them.
Point it at two ruler ticks and it refuses, because the gap between the ticks looks exactly like the background.

Switch the mode to **Between marks (centre to centre)**. That finds the *centre* of every mark the line
crosses and measures from the first centre to the last, which is what a person reading a ruler means
by "from 6 to 7".

**Steps**

1. Set the scale first, and drag the reference line **as long as you can** — 3 cm or more — then type its real length.
   (Calibrating on exactly 6→7 and then measuring 6→7 always returns 10 mm. That is measuring itself, not a test.)
2. Draw the measurement line **through both marks**, at a height where the marks actually reach.
3. Set the mode to **Between marks**.
4. Read the graph: the faint gold lines are every mark found, the two red lines are the outer pair being measured.

**Measured accuracy** on a synthetic ruler at 41 px/mm, calibrated over 20 mm, then measuring other spans
as independent checks:

| Span | Reported | Error |
|---|---|---|
| 1 mm | 0.99 | 0.01 mm |
| 5 mm | 4.98 | 0.02 mm |
| 10 mm | 10.00 | 0.00 mm |
| 30 mm | 30.00 | 0.00 mm |

Those come from synthetic images where everything is controlled. A real camera with a real lens at a real
angle will do worse; measure a known standard on site to find out by how much.

---

### How long should the reference line be

As long as you can make it. A few pixels of sloppiness when dragging the reference by hand gets divided
by its length, so a longer reference shrinks the error in proportion.

Measured by deliberately misplacing one end of the reference by 3 pixels, then measuring a true 10.000 mm span:

| Reference spans | Reading | Error |
|---|---|---|
| 5 mm | 9.854 | −0.146 mm |
| 10 mm | 9.926 | −0.074 mm |
| 20 mm | 9.962 | −0.038 mm |

**Rule of thumb**: make the reference at least a third of the image width, and never calibrate on the same
span you intend to measure — that always reads correct and proves nothing.

### How do I know the reading is right

1. **Check the mark count** shown under the value. Measuring 6→7 cm on a ruler with millimetre ticks should
   report 11 marks (11 marks = 10 intervals = 10 mm). Twelve means the line reached one tick too far.
2. **Look at the red lines on the graph** — they should sit exactly on the 6 and the 7.
3. **Measure something you already know.** Calibrate over 3 cm, then measure 1 cm. If it says 10.0 mm, the
   setup is sound.

---

**Two numbers under the value tell you whether to trust it**

The readout shows the **mark count** and the **distance per gap**.

* Measuring 6→7 cm on a millimetre ruler must show **11 marks** and **1.00 mm per gap**
* If it shows 10 marks the line missed an end tick, and the value will be 9 mm — **wrong with no warning**,
  because the per-gap figure still reads 1.00. The mark count is the only thing that catches it.

**The height of the line matters.** It has to sit where the marks physically reach: too high and it clips
only the tips or misses them, too low and it leaves the scale. Tested on the real screenshot: at the right
height it finds 11 marks, but 8 pixels higher it finds only 10.

---

### Stop guessing — press Auto-tune

Select a measurement line and look at the **Accuracy check** panel on the right — the same panel the
digit reader uses.

**Auto-tune** sweeps all three knobs — scan width, smooth and minimum contrast, 160 combinations — and
keeps the one that **reads the same value across the widest range**, not the one that happens to read
something once.

In "Between marks" mode there is a second piece of evidence: a real scale has evenly spaced marks. The
tuner scores that regularity too, so a setting that finds two random blobs cannot beat a tidy row of ticks.

**If nothing works it says so**, rather than emitting a number: the line probably is not crossing the marks.
And if it does find something whose spacing is more than 15% irregular, it flags the result as not yet
trustworthy.

Tested on the real ruler photo from a user's screen across 6 cases: auto-tune got 5 right. The one it
missed had the line dropping off the edge of the scale at one end — visible immediately from the red
markers on the graph.

**The starting values changed too**, based on those real-image tests: smooth 3 and minimum contrast 20,
which works straight away in many situations.

---

### Counting across a line

Draw the line across the walkway. **The gold arrow on the line is the direction counted as "in"** —
what you see is what gets counted. To reverse it, set `invert` rather than redrawing.

Counting needs a detector model, chosen in the **Detector for this camera** panel on the left.
The model runs once per frame and its boxes are shared by every line and zone, which is why a
Raspberry Pi can keep up.

A tracker is built in, so the same person is never counted twice, and nobody is counted for
walking up to the line and turning back, or for passing beyond its end.

Output can be `all` (in/out/net/total), `net`, `in`, `out` or `total`.

### Resetting the counters by shift or by day

A counter that never resets is of no use to a supervisor who wants last night's figure.
**Runtime → Reset counters** sets the boundary:

| Mode | Boundary | Period label |
|---|---|---|
| Never | the counters run until the program stops | — |
| Per shift | each time listed in *Shift start times*, e.g. `06:00, 14:00, 22:00` | `2026-10-10 A` |
| Daily | once a day at *Reset at* | `2026-10-10` |
| Hourly | on the hour | `2026-10-10 15h` |
| Every N minutes | every N minutes, counted from midnight | `2026-10-10 15:30` |

A shift that starts at 22:00 and ends at 06:00 keeps the date it **started** on, which is how
the handover sheet on the wall reads it.

Two sets of numbers are reported, always:

```json
{"in": 12, "out": 3, "net": 9, "total": 15,
 "period": "2026-10-10 A", "lifetime_in": 1840, "lifetime_out": 1792}
```

`in` / `out` are this shift. `lifetime_in` / `lifetime_out` are the running total since
installation and are never reset — which is what makes a restart visible instead of silent.

At each boundary, before the counter returns to zero, the closing numbers are published as
their own event:

```
topic   tesr/vision/<camera>/event/period_end
```
```json
{"time": "2026-10-10T14:00:01", "camera": "cam1", "event": "period_end",
 "roi": "door_gate", "period": "2026-10-10 A",
 "period_start": "2026-10-10T06:00:00", "period_end": "2026-10-10T14:00:01",
 "in": 12, "out": 3, "net": 9, "total": 15,
 "lifetime_in": 1840, "lifetime_out": 1792}
```

The same record goes to the JSONL and CSV logs and to the webhook, so a Node-RED flow can
file a shift against the shift that produced it instead of having to notice that a counter
went back to zero.

### Surviving a power cut

Set **Counter state file** — `logs/counters_{camera}.json` — and the counts are written to
disk, atomically, at most once every five seconds and once more on shutdown. On startup:

- restarted **inside** the same shift → the shift count is picked back up
- restarted **after** the shift changed → the new shift starts at zero, lifetime total intact
- `--reset-counters` on the command line → ignore the file and start clean

`{camera}` in the path is replaced with the camera id, so several cameras can share one setting.

A per-ROI override is available in the config for the rare case where two gates on one camera
work different shifts: put the same block under that ROI's `params.reset`.

```json
"runtime": {
  "counter_reset": {
    "mode": "shift",
    "shifts": ["06:00", "14:00", "22:00"],
    "state_file": "logs/counters_{camera}.json"
  }
}
```

Shifts can also be named, which is clearer in a dashboard than A/B/C:

```json
"shifts": [{"at": "08:00", "name": "day"}, {"at": "20:00", "name": "night"}]
```

### Counting inside a zone

Drag out a box then move the points to fit the area; `+ point` and `− point` adjust the corners.
Set the object anchor to **bottom edge (feet)** when counting people standing on a floor — the
box centre can fall outside the zone while the feet are inside it.

### One extra file

These tasks use `tesr_line_engine.py`, which must sit next to `tesr_vision_runner.py`.
If it is missing the runner says so at startup instead of failing partway through.

---

## 6.8 Reading a licence plate

The `plate_ocr` task is OCR with the plate alphabet and the plate format enforced on top.

**Draw the box around the registration row only.** Leave the province, state or country
name out of it: `psm 7` reads one line of text, and a second line of different characters
below the number is the most common reason a plate reads as nonsense.

What it does before the OCR engine sees the crop:

1. upscales to **Upscale to height** — a plate in a gate camera is often 20–40 px tall, well
   under what Tesseract needs, and upscaling first does more than any parameter tuning later
2. reduces noise, then evens out the light (CLAHE), which rescues a hazy or half-shaded plate
3. binarises, taking the polarity from the border of the crop — the border of a plate crop is
   background, so it says whether this is dark characters on a light plate or the reverse

**Plate format is the safety net, and the reason to use this task rather than plain OCR.**

```json
"params": {"pattern": "^[A-Z]{2}[0-9]{4}$", "min_chars": 4, "max_chars": 12}
```

A reading that cannot match the format is **withheld**, not reported — a wrong plate number
in a gate log is worse than no plate number. Where the format is unambiguous, one confusable
character is swapped and retried (`O`↔`0`, `I`↔`1`, `S`↔`5`, `B`↔`8`, `Z`↔`2`, `G`↔`6`), and
only accepted if the format then fits. Without a `pattern`, nothing is swapped and nothing is
format-checked; you get the cleaned characters as read.

### Plates that are not A–Z 0–9

A Thai plate carries Thai characters, and the same applies to Arabic, Cyrillic or Chinese
plates. Pick the language from the **Language** list in the Studio - it is a list, not a text
box, since v1.8.2 (a typed `th` asked Tesseract for a language that does not exist). Picking
**Thai (tha)** also switches the allowed characters to 0-9 plus the 44 Thai consonants, as long
as you have not edited that list yourself. On the device Tesseract needs the language pack:

```bash
sudo apt install tesseract-ocr-tha      # Debian / Raspberry Pi OS
brew install tesseract tesseract-lang   # macOS
```

**Everything inside the venv (Windows, Linux PC, Raspberry Pi, Jetson):**

```bash
pip install tesserocr
```

That wheel carries Tesseract itself, so nothing is installed on the system and no PATH is
edited. Since v1.8.4 the runner uses it first, and on the first run downloads any language the
config needs (`tha.traineddata`, 1 MB) into a `tessdata` folder beside you - the tessdata_fast
file, byte-for-byte what `tesseract-ocr-tha` installs. Offline machines: run
`python tesr_vision_runner.py --get-lang tha` once where there is internet and copy `tessdata`.
macOS has no current tesserocr wheel: there use `brew install tesseract tesseract-lang` and
`pip install pytesseract`. Without tesserocr the runner falls back to the tesseract program and
finds it in Program Files or Homebrew by itself. Either way it checks the engine and every
language once at startup and says what to do, instead of warning every frame.

```json
"params": {"engine": "tesseract", "lang": "tha",
           "whitelist": "0123456789กขฃคฅฆงจฉชซฌญฎฏฐฑฒณดตถทธนบปผฝพฟภมยรลวศษสหฬอฮ"}
```

Use `tha` on its own rather than `tha+eng`: on the same plate with Tesseract 5, `tha` read 35
of 36 box positions correctly and `tha+eng` lost the Thai letters on 21 of 36. The runner
accepts `en` / `th` too and maps them for each engine (Tesseract wants `eng` / `tha`, easyocr
wants `en` / `th`, and loads Thai only alongside English).

The browser preview reads Thai as well: Tesseract.js fetches the Thai data the first time
(needs internet). If that download fails, the preview says which language could not be loaded
and stays usable. It is still a tuning aid - confirm the reading with Python on the device.

### Try it

```bash
cd runner
python tesr_vision_runner.py --config ../examples/plate_config.json --once
```

`examples/plate.jpg` is a drawn sample, not a photograph of a real vehicle.

**What this is not.** This is a plate *reader* for one region you point at a plate. It is not
a full ANPR system: it does not find the plate in the frame for you, and it does not follow a
car across frames. For a moving vehicle, put a detector model on the camera, use a counting
line to decide when the car is in position, and read the plate from a ROI covering that spot —
or train a plate-detector model and crop from its box.

---

## 6.9 Reading an analogue needle gauge

Plenty of machines still have a round dial and no output of any kind. `gauge_needle` reads one
with a rectangular ROI, no model and no training — the dial equivalent of `level_line`, with an
angle in place of a position along a line.

**Draw the ROI square around the dial face.** Not the bezel, not the pipework: the painted face,
as tight as you can get it while still containing the whole needle sweep. The task works in a
circle that fits inside the box, so a box much larger than the dial wastes radius and a box that
clips the needle loses it.

**Then give it two reference angles.** Exactly like `Value at A` / `Value at B` on a tank:

| Setting | Means |
|---|---|
| `min_angle` / `min_value` | Where the first tick sits, and what it reads |
| `max_angle` / `max_value` | Where the last tick sits, and what it reads |

Angles are in degrees, **0° points right and the angle grows anticlockwise** — so 90° is straight
up, 180° is left, 225° is bottom-left and −45° is bottom-right. Most gauges sweep clockwise from
about 225° round to about −45°, which is the default, and most of the time the only two numbers
you change are `min_value` and `max_value`. The sweep direction is not a separate setting: it
falls out of the two angles. A gauge that runs the other way is written `min_angle: -45`,
`max_angle: 225`, and nothing else changes.

The dial centre is `center_x` / `center_y`, as a fraction of the ROI, defaulting to the middle of
the box. It has to sit on the pin the needle turns about. Get it slightly wrong and every reading
shifts; get it badly wrong and the task stops reporting, because the rays it casts no longer run
along the needle.

### How it finds the needle

It sweeps a ray out from the hub in every direction and keeps the direction in which
needle-coloured pixels run furthest from the centre. A needle is the only mark on a dial that is
both long and joined to the hub, so tick marks, printed numbers and a painted warning band near
the rim never win — none of them touches the middle. That is the whole method: no model, no
training, one threshold and one minimum length.

| Setting | Default | What it is for |
|---|---|---|
| `threshold_mode` / `threshold` | `otsu` / 128 | `otsu` picks the split between needle and face automatically. Switch to `fixed` when the light on the dial changes through the day and you want the same number every time |
| `needle` | `dark` | A dark needle on a light face, or `light` for the reverse |
| `pointer` | `long` | Which end of the needle to read. Some gauges carry a counterweight tail longer than the pointer; if the reading comes out half a turn wrong, switch to `short` |
| `min_length` | 0.35 | How far a mark must reach, as a fraction of the dial radius, before it is believed to be the needle |
| `scan_inner` / `scan_outer` | 0.12 / 0.92 | The band of radius that is searched. Raise `scan_inner` past a large hub; lower `scan_outer` inside a crowded tick ring |
| `max_fill` | 0.35 | If more of the face than this is needle-coloured, the threshold is wrong or this is not a dial, and nothing is reported |
| `clamp` | `true` | What to do when the needle points into the dead sector below the hub: round to the nearer end of the scale, or report nothing |
| `unit` / `decimals` | `""` / 1 | Rounding, and the unit shown in the preview. The published value is the bare number, so CSV and MQTT stay numeric |

### When it cannot find the needle

It reports **nothing at all** — no last value, no zero, no guess. The ROI shows `—`, the console
line shows `-`, and no row is written. That happens when the needle is shorter than `min_length`,
when too much of the face is needle-coloured, when the ROI is too small to hold a dial, and, with
`clamp` off, when the needle points outside the scale. A gap in a log is a fact you can act on; a
plausible wrong pressure is not.

### Calibrating it in the browser

Draw the ROI, pick `gauge_needle`, and press **Test now**. The preview draws two gold spokes at
`min_angle` and `max_angle` and a red one on the needle it found. Nudge the two angles until the
gold spokes lie along the first and last tick of the printed scale, type the values those ticks
read, and the red spoke tells you the rest. The browser and `tesr_vision_runner.py` use the same
method, so what you read in the preview is what the deployed runner reads.

Raise `stable_frames` for a needle that trembles — a reading has to repeat that many frames
before it is published, exactly as for every other task.

> Stated plainly for customers: the only figure this project quotes for the gauge reader is the
> one `tests/gauge_test.py` asserts on dials it draws itself, where the needle angle is known
> exactly. A real gauge has curved glass, a reflection of the window, a needle that sits a few
> millimetres above the face and a camera that is never quite square to it. What the reader
> achieves on a real installation has to be established by comparing it against the dial, on
> site, over a shift.

---

## 7. Common problems on site

| Symptom | Usual cause | Fix |
|---|---|---|
| `The function is not implemented. Rebuild the library with...` | `opencv-python-headless` is installed, which has no GUI | Nothing to fix — the runner detects this, says so once and carries on without a window. Readings still reach the console and every configured output. `--calibrate` is the one thing that cannot work; tune in the browser instead |
| Some digits read as `?` | Threshold is off, or the ROI hugs the digits too tightly | `--calibrate` to adjust threshold/min_fill; widen the ROI a little |
| Wrong number of digits | The gaps between digits are narrow and get merged | Set `digits` to the real count, or switch `split` to fixed |
| The value keeps changing | Flickering light or a camera that moves | Raise `stable_frames`, mount the camera rigidly, kill the glare |
| ROI labels overlap and cannot be read | Fixed: values now sit inside their box and the summary moved to a panel below the image | Press `p` to hide or show the panel |
| Console output is scattered and hard to follow | The old mode printed one line per ROI | Use `--console summary`, which is now the default |
| Dark or reflective image | The camera is at an angle to the display glass | Tilt 10–20°, add a shade hood, avoid direct lighting |
| Low FPS on a Pi | Camera resolution higher than needed | Lower `width`/`height`, set `loop_fps`, use `every_n_frames` |
| The camera will not open in the browser | The browser is refusing permission | Use Chrome or Edge and allow the camera; meanwhile load an image instead |
| "Could not load the engine" after adding a model | The machine is offline | The ONNX engine is fetched once; connect and reload the page |
| The model runs in the browser but Python cannot find it | The command was run from a different folder, or only the config was copied out of the zip | `cd` into the unzipped folder and run from there — a relative model path is opened from the current folder (the warning prints which folder that is) |
| Every button in the page is dead | A script error | A red bar appears at the top with the message; press F12 → Console for details |

---

## 8. Limits worth stating plainly to a customer

* Real accuracy depends on **lighting, camera angle, distance and how crisp the machine's display is**. It can only be measured on site. Any figure quoted before testing is an estimate.
* `digits_7seg` is built for 7-segment shapes. For an ordinary digital font or an analogue needle, use `ocr_text` or train a model instead.
* If the camera is moved after installation, the ROIs must be redrawn. Design the mount to lock its position.
* Reading in the browser is a tuning aid. Confirm the real value with Python on the target device.

---

## 9. Where this fits at TESR

* **TESR Academy** — a ready-made lab for *AI Vision Engineering for Edge AI*: day 1 draw ROIs, read values and test; day 2 deploy to Jetson/Pi, publish to Node-RED and a dashboard.
* **TESR Playground** — test camera distance, lens and lighting against a customer's real machine display before quoting.
* **TESR Shop** — bundle a Machine Reading Kit (edge computer, camera, mount, shade hood). Always confirm stock with the team before promising it to a customer.
* **TESR Solution** — grow into OEE capture, production counting and machine monitoring without touching the customer's PLC.
* **Content** — "point a camera at a machine display, get the number into a dashboard" is a demonstration piece that shows a result in 30 seconds.

---

## 10. Corporate identity used in the tool

Every colour is sampled from the TESR logo file, not eyeballed.

| Role | Hex | Used for |
|---|---|---|
| TESR Red | `#F30006` | Primary buttons, section markers, ROIs with no value yet, YOLO ROIs |
| TESR Red deep | `#A00004` | Pressed and hover states |
| TESR Gold | `#E6CC81` | Rules, the selected ROI, digit ROIs, the readout digits |
| TESR Gold deep | `#927B49` | Hairlines and secondary text |
| Ink | `#1B1B1B` | Panel backgrounds and the overlay bar |
| Graphite | `#2D2D2D` / `#454545` | Borders and grid lines |
| White | `#F2F2F2` | Primary text |

* The logo is embedded in `index.html` as a data URI, so it renders offline. Its white background has been knocked out to transparency.
* The typeface is **Kanit**, per the brand. Offline machines without it fall back to Noto Sans Thai and then the system stack.
* The `tesr_vision_runner.py` window carries a TESR bar with a gold-and-red mark drawn in OpenCV, so no external image file is needed. Handy for demo recordings and screenshots sent to customers.
* ROI colours lead with gold and red; the remaining hues are muted to stay in the same family while remaining distinguishable on a real photograph.

---

TESR Co., Ltd. — Thai Embedded Systems and Robotics
Build, Train and Deploy AI to Edge Devices
