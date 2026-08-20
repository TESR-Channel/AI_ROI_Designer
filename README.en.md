# TESR ROI Studio + Vision Runner

*ฉบับภาษาไทย: [README.th.md](README.th.md) · [Back to the repository README](README.md)*

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

**Or publish it on GitHub Pages** and open the link (see `github-pages/`). Better for sharing with a team or a customer, and the live camera always works because the page is served over https.

Python only enters the picture once you export and run on the target device.

The only thing that needs the internet is the **first use of an `.onnx` model or OCR**, because the engine is fetched then. Drawing ROIs, reading digits, colour checks, change detection, QR and barcodes all work fully offline.

---

## 1. Workflow

```text
Photograph the machine
  -> open roi_designer.html and drag out ROIs (many per camera, many cameras)
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

Everything is prepared in `github-pages/`.

```
github-pages/
  index.html                     the tool (identical to roi_designer.html)
  .nojekyll                      keeps Jekyll from touching the files
  README.md                      README for the repository
  .github/workflows/deploy.yml   deploys on every push
```

1. Create a repository and upload everything in that folder
2. **Settings → Pages → Source**, choose **GitHub Actions**
3. Push to `main` and wait about a minute

To publish a new version, replace `index.html` with the new `index.html` and push.

Exporting config and Python still works exactly the same, because those files are generated in the visitor's browser and never touch the server.

---

## 1.5 Using a model you trained

Press **+ Add a trained model** in the left panel, choose an `.onnx` file, type the class names, then assign that model to any YOLO task ROI. Press **Test now** and the detection boxes appear immediately; in live mode it keeps running.

Export from Ultralytics like this:

```bash
yolo export model=best.pt format=onnx opset=12
```

* The model stays in that browser on that machine. Nothing is uploaded anywhere.
* Exporting a config records the model filename and class names, but you still need to copy the `.onnx` file next to `tesr_vision_runner.py` yourself.
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
| `yolo_detect` | Detect objects inside the ROI, count them or return details | Your own `.onnx` |
| `yolo_classify` | Classify the ROI, e.g. OK / NG | Your own `.onnx` |
| `barcode_qr` | Read QR codes and linear barcodes | No — decoders are bundled on both sides |
| `presence_diff` | Detect a change or the presence of something | No |
| `color_check` | Check a status lamp, a tower light or part colour | No |

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

### Counting across a line

Draw the line across the walkway. **The gold arrow on the line is the direction counted as "in"** —
what you see is what gets counted. To reverse it, set `invert` rather than redrawing.

Counting needs a detector model, chosen in the **Detector for this camera** panel on the left.
The model runs once per frame and its boxes are shared by every line and zone, which is why a
Raspberry Pi can keep up.

A tracker is built in, so the same person is never counted twice, and nobody is counted for
walking up to the line and turning back, or for passing beyond its end.

Output can be `all` (in/out/net/total), `net`, `in`, `out` or `total`.

### Counting inside a zone

Drag out a box then move the points to fit the area; `+ point` and `− point` adjust the corners.
Set the object anchor to **bottom edge (feet)** when counting people standing on a floor — the
box centre can fall outside the zone while the feet are inside it.

### One extra file

These tasks use `tesr_line_engine.py`, which must sit next to `tesr_vision_runner.py`.
If it is missing the runner says so at startup instead of failing partway through.

---

## 7. Common problems on site

| Symptom | Usual cause | Fix |
|---|---|---|
| Some digits read as `?` | Threshold is off, or the ROI hugs the digits too tightly | `--calibrate` to adjust threshold/min_fill; widen the ROI a little |
| Wrong number of digits | The gaps between digits are narrow and get merged | Set `digits` to the real count, or switch `split` to fixed |
| The value keeps changing | Flickering light or a camera that moves | Raise `stable_frames`, mount the camera rigidly, kill the glare |
| ROI labels overlap and cannot be read | Fixed: values now sit inside their box and the summary moved to a panel below the image | Press `p` to hide or show the panel |
| Console output is scattered and hard to follow | The old mode printed one line per ROI | Use `--console summary`, which is now the default |
| Dark or reflective image | The camera is at an angle to the display glass | Tilt 10–20°, add a shade hood, avoid direct lighting |
| Low FPS on a Pi | Camera resolution higher than needed | Lower `width`/`height`, set `loop_fps`, use `every_n_frames` |
| The camera will not open in the browser | The browser is refusing permission | Use Chrome or Edge and allow the camera; meanwhile load an image instead |
| "Could not load the engine" after adding a model | The machine is offline | The ONNX engine is fetched once; connect and reload the page |
| The model runs in the browser but Python cannot find it | The `.onnx` file was never copied over | Put the `.onnx` in the same folder as `tesr_vision_runner.py` |
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
