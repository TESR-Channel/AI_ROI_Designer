# TESR ROI Studio

A browser tool to **draw ROIs on a photo or a live camera, read the values right there, and export Python that really runs on an edge device**.
Available in English and Thai — use the TH / EN switch in the header.

By TESR Co., Ltd. — Thai Embedded Systems and Robotics

**Open it:** [`https://<account>.github.io/<repo>/`](https://tesr-channel.github.io/AI_ROI_Designer/)
**Offline:** download `index.html` and double-click it. Nothing to install, no server, and no image or model ever leaves the machine.

### What it does

* Many ROIs per camera, many cameras per project, rotatable boxes
* A task per ROI: 7-segment digits · OCR · YOLO detect/classify · QR and barcodes · change detection · colour check
* Live camera in the middle of the screen with values attached to each ROI in real time
* Add your own trained `.onnx` model and see results immediately
* Auto-tune for digit reading, with plain-language reasons when a read is unreliable
* Exports `config.json` + `tesr_vision_runner.py` for Windows, macOS, Linux, Raspberry Pi and NVIDIA Jetson

### Publishing on GitHub Pages

1. Create a repository and upload everything in this folder (`index.html`, `.nojekyll`, `.github/`)
2. **Settings → Pages → Source**, choose **GitHub Actions**
3. Push to `main`; the workflow deploys in about a minute

To update, replace `index.html` with the newer `roi_designer.html` and push.

---

# ภาษาไทย

เครื่องมือหน้าเว็บสำหรับ **ตีกรอบ ROI บนภาพหรือกล้องสด → อ่านค่าได้ทันทีในเบราว์เซอร์ → ส่งออกเป็น Python ที่รันบน Edge Device ได้จริง**

โดย TESR Co., Ltd. — Thai Embedded Systems and Robotics

---

## เปิดใช้งาน

**บนเว็บ:** `https://<ชื่อบัญชี>.github.io/<ชื่อ repo>/`

**ออฟไลน์:** ดาวน์โหลด `index.html` ไฟล์เดียว แล้วดับเบิลคลิก ใช้ได้ครบเหมือนกัน

ไม่ต้องติดตั้งอะไร ไม่ต้องรันเซิร์ฟเวอร์ ไม่มีการอัปโหลดภาพหรือโมเดลไปที่ใด ทุกอย่างประมวลผลในเครื่องผู้ใช้

---

## ทำอะไรได้

* ตีกรอบ ROI ได้หลายกรอบต่อกล้อง หลายกล้องต่อโปรเจกต์ หมุนกรอบได้
* เลือกชนิดงานแยกต่อกรอบ: อ่านตัวเลข 7-segment · OCR · YOLO detect/classify · QR และบาร์โค้ด · ตรวจการเปลี่ยนแปลง · ตรวจสี
* ดูภาพจากกล้องสดตรงกลางจอ พร้อมค่าที่อ่านได้เกาะบนกรอบแบบเรียลไทม์
* เพิ่มโมเดล `.onnx` ที่เทรนเองแล้วดูผลได้ทันทีในเบราว์เซอร์
* ปุ่มปรับอัตโนมัติสำหรับการอ่านตัวเลข พร้อมบอกสาเหตุเป็นข้อ ๆ เมื่ออ่านไม่แม่น
* ส่งออก `config.json` + `tesr_vision_runner.py` ที่รันได้บน Windows, macOS, Linux, Raspberry Pi และ NVIDIA Jetson

---

## ตั้งค่า GitHub Pages

1. สร้าง repository ใหม่ แล้วอัปโหลดไฟล์ในโฟลเดอร์นี้ทั้งหมด (`index.html`, `.nojekyll`, `.github/`)
2. ไปที่ **Settings → Pages**
3. ที่ **Source** เลือก **GitHub Actions**
4. push ขึ้น branch `main` — workflow จะ deploy ให้เอง ใช้เวลาราวหนึ่งนาที

หรือถ้าไม่อยากใช้ Actions: ที่ **Settings → Pages** เลือก **Deploy from a branch** → `main` → `/ (root)` ก็ได้เหมือนกัน ไฟล์ `.nojekyll` มีไว้กัน Jekyll เข้ามายุ่งกับไฟล์

### อัปเดตเวอร์ชันใหม่

แทนที่ `index.html` ด้วยไฟล์ `roi_designer.html` ตัวใหม่ (เปลี่ยนชื่อเป็น `index.html`) แล้ว push

---

## ทำไมควรใช้ผ่าน Pages มากกว่าเปิดไฟล์ตรง ๆ

| | เปิดไฟล์ (`file://`) | ผ่าน GitHub Pages (`https://`) |
|---|---|---|
| ตีกรอบ อ่านตัวเลข QR บาร์โค้ด ตรวจสี | ได้ | ได้ |
| ส่งออก config + Python | ได้ | ได้ |
| กล้องสด | ขึ้นกับเบราว์เซอร์ | ได้เสมอ เพราะเป็น https |
| แชร์ให้ทีมหรือลูกค้า | ต้องส่งไฟล์ | ส่งลิงก์เดียว |
| โมเดล `.onnx` และ OCR | ต้องต่อเน็ตเพื่อโหลดเอนจินครั้งแรก | เหมือนกัน |

---

## ความเป็นส่วนตัว

ภาพจากกล้อง ภาพที่อัปโหลด และไฟล์โมเดล **ไม่เคยถูกส่งออกจากเครื่องผู้ใช้** โมเดลถูกเก็บใน IndexedDB ของเบราว์เซอร์เครื่องนั้นเท่านั้น
สิ่งเดียวที่ดึงจากอินเทอร์เน็ตคือไฟล์เอนจิน ONNX และ OCR จาก CDN เมื่อใช้ฟีเจอร์นั้นครั้งแรก

---

## เครดิต

* ตัวถอดรหัส QR ใช้ [jsQR](https://github.com/cozmo/jsQR) (Apache-2.0) ฝังมาในไฟล์
* ตัวถอดบาร์โค้ดแท่ง (Code 128, Code 39, EAN-13, UPC-A, EAN-8, ITF) เขียนขึ้นเองสำหรับเครื่องมือนี้
* โมเดล ONNX รันด้วย [onnxruntime-web](https://onnxruntime.ai/) · OCR ใช้ [Tesseract.js](https://tesseract.projectnaptha.com/)

---

TESR Co., Ltd. — Build, Train and Deploy AI to Edge Devices
