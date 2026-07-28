# TESR ROI Studio

A browser tool to **draw ROIs on a photo or a live camera, read the values right there, and export Python that really runs on an edge device**.
Available in English and Thai — use the TH / EN switch in the header.

By TESR Co., Ltd. — Thai Embedded Systems and Robotics

**Open it:** https://tesr-channel.github.io/AI_ROI_Designer/

**Offline:** download `index.html` and double-click it. Nothing to install, no server, and no image or model ever leaves the machine.

### What it does

* Many ROIs per camera, many cameras per project, rotatable boxes
* A task per ROI: 7-segment digits · OCR · YOLO detect/classify · QR and barcodes · change detection · colour check
* Live camera in the middle of the screen with values attached to each ROI in real time
* Add your own trained `.onnx` model and see results immediately
* Auto-tune for digit reading, with plain-language reasons when a read is unreliable
* Exports `config.json` + `tesr_vision_runner.py` for Windows, macOS, Linux, Raspberry Pi and NVIDIA Jetson

---

TESR Co., Ltd. — Build, Train and Deploy AI to Edge Devices
