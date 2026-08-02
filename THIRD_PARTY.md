# Third-party components

`index.html` is a single self-contained file. Two third-party components are
bundled inside it so the tool works offline.

| Component | Purpose | Licence |
|---|---|---|
| [jsQR](https://github.com/cozmo/jsQR) 1.4.0 | QR code decoding in the browser | Apache-2.0 |

Everything else in this repository — the 1-D barcode decoder (Code 128, Code 39,
EAN-13, UPC-A, EAN-8, ITF), the 7-segment reader, the auto-tuner, the line and
zone engine, the tracker and the Python runner — was written for this project
and is covered by the MIT licence in `LICENSE`.

## Loaded from a CDN on first use, not bundled

These are fetched only when you use the feature that needs them, and are not
redistributed here.

| Component | Used for | Licence |
|---|---|---|
| [onnxruntime-web](https://onnxruntime.ai/) | Running your `.onnx` models in the browser | MIT |
| [Tesseract.js](https://tesseract.projectnaptha.com/) | OCR in the browser | Apache-2.0 |
| [Kanit](https://fonts.google.com/specimen/Kanit) via Google Fonts | The TESR brand typeface | SIL Open Font License 1.1 |

## Apache-2.0 notice for jsQR

Licensed under the Apache License, Version 2.0. You may obtain a copy of the
License at http://www.apache.org/licenses/LICENSE-2.0
