# Third-party components

`index.html` is a single self-contained file. Two third-party components are
bundled inside it so the tool works offline.

| Component | Purpose | Licence |
|---|---|---|
| [jsQR](https://github.com/cozmo/jsQR) 1.4.0 | QR code decoding in the browser | Apache-2.0 |

Everything else in this repository — the 1-D barcode decoder (Code 128, Code 39,
EAN-13, UPC-A, EAN-8, ITF), the 7-segment reader, the auto-tuner, the line and
zone engine, the tracker, the shift clock, the licence plate reader and the
Python runner — was written for this project and is covered by the MIT licence
in `LICENSE`.

## Loaded from a CDN on first use, not bundled

These are fetched only when you use the feature that needs them, and are not
redistributed here.

| Component | Used for | Licence |
|---|---|---|
| [onnxruntime-web](https://onnxruntime.ai/) | Running your `.onnx` models in the browser | MIT |
| [Tesseract.js](https://tesseract.projectnaptha.com/) | OCR in the browser | Apache-2.0 |
| [Kanit](https://fonts.google.com/specimen/Kanit) via Google Fonts | The TESR brand typeface | SIL Open Font License 1.1 |

## Used by the runner only when a task needs it

Installed by the user on the target device, never redistributed here.

| Component | Used for | Licence |
|---|---|---|
| [onnxruntime](https://onnxruntime.ai/) | `.onnx` models on the device | MIT |
| [pytesseract](https://github.com/madmaze/pytesseract) + the Tesseract engine | `ocr_text`, `plate_ocr` | Apache-2.0 (both) |
| [EasyOCR](https://github.com/JaidedAI/EasyOCR) | `ocr_text`, `plate_ocr` with non-Latin characters | Apache-2.0 |
| [paho-mqtt](https://eclipse.dev/paho/) | publishing readings and shift events | EPL-2.0 / EDL-1.0 |
| [OpenCV](https://opencv.org/) and [NumPy](https://numpy.org/) | required by the runner | Apache-2.0 / BSD-3-Clause |

## Used by the tests only

| Component | Used for | Licence |
|---|---|---|
| [jsdom](https://github.com/jsdom/jsdom) | loading `index.html` in a headless DOM | MIT |

## Apache-2.0 notice for jsQR

Licensed under the Apache License, Version 2.0. You may obtain a copy of the
License at http://www.apache.org/licenses/LICENSE-2.0
