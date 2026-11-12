#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write node-red/vision_dashboard_flow.json - the Node-RED dashboard that shows what
tesr_vision_runner.py sees, instead of the Python window.

    python3 tools/make_dashboard_flow.py

Needs in Node-RED (Menu > Manage palette > Install): node-red-dashboard and
node-red-contrib-aedes (the broker lives inside Node-RED, as in the course).
Then on the camera computer:
    python tesr_vision_runner.py --camera usb --source 0 --broker <Node-RED computer> --headless
Dashboard: http://<Node-RED computer>:1880/ui
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "node-red", "vision_dashboard_flow.json")
Z = "tv_tab"
BROKER = "tv_mqtt"

INFO = """TESR Vision dashboard - what tesr_vision_runner.py sees, on a web page.

1. Node-RED: Menu > Manage palette > Install node-red-dashboard and node-red-contrib-aedes
2. Menu > Import > this file > Import > Deploy  (the aedes node IS the MQTT broker, port 1883)
3. On the camera computer, in the exported project folder, with the venv open:
     python tesr_vision_runner.py --camera usb --source 0 --broker <this computer> --headless
   (--broker localhost when the runner and Node-RED are on the same computer;
    --headless = no Python window, the dashboard is the screen)
4. Open http://<this computer>:1880/ui

The runner sends the camera picture (with the boxes) on tesr/vision/<camera>/frame
4 times a second, and one message per region on tesr/vision/<camera>/<region>
whenever its reading changes. "AI 2 layers" regions (parts_check) are counted
OK / NG - one part each time a part comes into view (after "no part"); a verdict
that changes while the part is still there corrects that part, it is not a new
one. Any other region shows its latest value. Made for a live camera / video:
a --image FOLDER run has no "no part" between pictures.

Already have an aedes broker node in another flow (chapter 12 / 13)? Delete one -
two of them fight for port 1883."""


def node(id_, type_, **kw):
    d = {"id": id_, "type": type_}
    if type_ not in ("mqtt-broker", "ui_group", "ui_tab"):
        d["z"] = Z
    d.update(kw)
    return d


CAMERA = r"""<style>
.tv-cam { position: relative; width: 100%; }
.tv-cam img { width: 100%; height: 500px; object-fit: contain; background: #0f172a; border-radius: 10px; display: block; }
.tv-wait { padding: 70px 10px; text-align: center; opacity: .65; font-size: 15px; line-height: 1.7; }
.tv-badge { position: absolute; top: 10px; left: 10px; padding: 3px 12px; border-radius: 14px;
            font-size: 13px; font-weight: 700; color: #fff; background: #16a34a; }
.tv-badge.off { background: #dc2626; }
</style>
<div class="tv-cam">
  <img ng-if="msg.payload" ng-src="data:image/jpeg;base64,{{msg.payload}}">
  <div ng-if="!msg.payload" class="tv-wait">รอภาพจากกล้อง...<br>
    <code>python tesr_vision_runner.py --camera usb --source 0 --broker &lt;เครื่องนี้&gt; --headless</code></div>
  <span ng-if="msg.payload" class="tv-badge" ng-class="{off: age > 3}">
    {{ age > 3 ? 'ภาพหยุด ' + age + ' วินาที - runner ปิดอยู่?' : '● LIVE ' + (msg.camera || '') }}</span>
</div>
<script>
(function (scope) {
    var last = Date.now();
    scope.age = 0;
    scope.$watch('msg', function (m) { if (m && m.payload) last = Date.now(); });
    var t = setInterval(function () {
        scope.$apply(function () { scope.age = Math.round((Date.now() - last) / 1000); });
    }, 1000);
    scope.$on('$destroy', function () { clearInterval(t); });
})(scope);
</script>
"""

NOW = r"""<style>
.tv-card { border-radius: 14px; padding: 10px 12px; margin-bottom: 10px; color: #fff; }
.tv-card.OK { background: #16a34a; } .tv-card.NG { background: #dc2626; }
.tv-card.none, .tv-card.value { background: #475569; }
.tv-card .big { font-size: 46px; font-weight: 800; line-height: 1.05; }
.tv-card .sub { font-size: 14px; opacity: .92; }
.tv-card .val { font-size: 26px; font-weight: 700; word-break: break-all; }
.tv-card .chip { display: inline-block; margin: 2px 4px 2px 0; padding: 2px 10px; border-radius: 12px;
                 font-size: 17px; font-weight: 700; background: rgba(255,255,255,.22); }
.tv-card .chip.ng { background: #7f1d1d; }
</style>
<div ng-if="!msg.payload || !msg.payload.length" style="opacity:.6; padding:20px 0; text-align:center">ยังไม่มีผล</div>
<div ng-repeat="c in msg.payload" class="tv-card" ng-class="c.kind == 'parts' ? c.result : 'value'">
  <div class="sub">{{c.roi}} · {{c.camera}} · {{c.time}}</div>
  <div ng-if="c.kind == 'parts' && c.result != 'none'">
    <div class="big">{{c.result}} <span ng-if="c.found.length == 1" style="font-size:34px">{{c.found[0]}}/{{c.expect}}</span></div>
    <div ng-if="c.found.length > 1" style="margin:4px 0">
      <span ng-repeat="n in c.found track by $index" class="chip" ng-class="n == c.expect ? 'ok' : 'ng'">#{{$index + 1}} {{n}}/{{c.expect}}</span></div>
    <div class="sub">เจอ {{c.objects}} ชิ้น · ครบ = {{c.expect}} · conf {{c.conf}}</div>
  </div>
  <div ng-if="c.kind == 'parts' && c.result == 'none'" class="big" style="font-size:30px">ไม่มีชิ้นงาน</div>
  <div ng-if="c.kind == 'value'" class="val">{{c.text}} <span ng-if="c.verdict" class="sub">[{{c.verdict}}]</span></div>
</div>
"""

COUNT = r"""<style>
.tv-row { display: flex; gap: 10px; }
.tv-box { flex: 1; border-radius: 12px; padding: 6px; text-align: center; border: 2px solid rgba(127,127,127,.25); }
.tv-box .n { font-size: 40px; font-weight: 800; line-height: 1.15; }
.tv-bar { height: 26px; border-radius: 13px; background: rgba(220,38,38,.35); overflow: hidden; margin: 10px 0 4px; }
.tv-fill { height: 100%; background: #16a34a; color: #fff; font-weight: 700; line-height: 26px;
           text-align: right; padding-right: 10px; box-sizing: border-box; transition: width .3s; }
</style>
<div class="tv-row">
  <div class="tv-box" style="border-color:#16a34a"><div>OK</div><div class="n" style="color:#16a34a">{{msg.payload.ok || 0}}</div></div>
  <div class="tv-box" style="border-color:#dc2626"><div>NG</div><div class="n" style="color:#dc2626">{{msg.payload.ng || 0}}</div></div>
  <div class="tv-box"><div>รวม</div><div class="n">{{(msg.payload.ok || 0) + (msg.payload.ng || 0)}}</div></div>
</div>
<div class="tv-bar"><div class="tv-fill" ng-style="{width: msg.payload.yield + '%'}">{{msg.payload.yield}}% OK</div></div>
<div style="font-size:12px; opacity:.7">นับตั้งแต่ {{msg.payload.since || '-'}} · นับ 1 ชิ้นเมื่อชิ้นงานเข้ามา (หลัง &quot;ไม่มีชิ้นงาน&quot;) · ผลเปลี่ยนระหว่างอยู่ใต้กล้อง = แก้ผลชิ้นเดิม</div>
"""

LAST_NG = r"""<div style="text-align:center; overflow:hidden">
  <div ng-if="!msg.payload || !msg.payload.time" style="padding:30px 0; opacity:.6">ยังไม่มี NG</div>
  <div ng-if="msg.payload && msg.payload.time">
    <img ng-if="msg.payload.pic" ng-src="data:image/jpeg;base64,{{msg.payload.pic}}"
         style="width:100%; height:200px; object-fit:contain; background:#0f172a; border-radius:10px; border:4px solid #dc2626; display:block; box-sizing:border-box">
    <div style="font-size:20px; font-weight:700; color:#dc2626; margin-top:6px">
      NG {{msg.payload.found}} · {{msg.payload.roi}}</div>
    <div style="font-size:13px; opacity:.7">{{msg.payload.time}}</div>
  </div>
</div>
"""

HIST = r"""<style>
.tv-t { width: 100%; border-collapse: collapse; font-size: 14px; }
.tv-t td, .tv-t th { padding: 3px 6px; border-bottom: 1px solid rgba(127,127,127,.2); text-align: left; }
.tv-t .OK { color: #16a34a; font-weight: 700; } .tv-t .NG { color: #dc2626; font-weight: 700; }
</style>
<div ng-if="!msg.payload || !msg.payload.length" style="opacity:.6; padding:20px 0; text-align:center">ยังไม่มีประวัติ</div>
<table class="tv-t" ng-if="msg.payload.length">
  <tr><th>เวลา</th><th>ผล</th><th>นับได้ (ต่อชิ้น)</th><th>กรอบ</th></tr>
  <tr ng-repeat="h in msg.payload"><td>{{h.time}}</td><td ng-class="h.result">{{h.result}}</td>
      <td>{{h.found}}</td><td>{{h.roi}}</td></tr>
</table>
"""

FRAME_FN = r"""// The picture from the runner: tesr/vision/<camera>/frame, a JPEG as base64 text.
// Kept per camera, so an NG can be shown with the picture it happened on.
const cam = msg.topic.split("/").slice(-2)[0];
const pics = flow.get("pics") || {};
pics[cam] = msg.payload;
flow.set("pics", pics);
msg.camera = cam;
return msg;
"""

READING_FN = r"""// One reading from tesr_vision_runner.py --broker <this computer>:
// topic tesr/vision/<camera>/<region>, payload {time, camera, roi, key, value, conf}.
// On a camera a reading is sent when it CHANGES (a part comes, goes, or is read again).
const r = msg.payload;
if (!r || typeof r !== "object" || !("value" in r)) return null;   // the picture, an event
const v = r.value;
const parts = !!(v && typeof v === "object" && "result" in v);    // an "AI 2 layers" region
const time = String(r.time || "").replace("T", " ").slice(0, 19);
const card = {id: r.camera + "/" + r.key, roi: r.roi, camera: r.camera, time: time.slice(11),
              conf: r.conf, kind: parts ? "parts" : "value"};
if (parts) Object.assign(card, {result: v.result, found: v.found || [], expect: v.expect, objects: v.objects});
else Object.assign(card, {text: typeof v === "object" ? JSON.stringify(v) : String(v), verdict: r.verdict});
const now = flow.get("now") || {};
now[card.id] = card;
flow.set("now", now);

const out = [{payload: Object.values(now)}, null, null, null];
if (parts) {
    // One part = one visit: it is counted when it comes into view (after "no part"),
    // and if its verdict changes while it is still there, the count moves with it.
    const visits = flow.get("visits") || {};
    const c = flow.get("qc") || {ok: 0, ng: 0, since: time};
    let hist = flow.get("hist") || [];
    const vis = visits[card.id];
    const found = (v.found || []).map(n => n + "/" + v.expect).join(" · ");
    const row = {time: time.slice(11), result: v.result, found, expect: v.expect, roi: r.roi};
    if (v.result === "none") {
        delete visits[card.id];
        node.status({fill: "grey", shape: "ring", text: "OK " + c.ok + " · NG " + c.ng + " · no part in view"});
    } else {
        if (!vis) {                                     // a new part
            c[v.result === "OK" ? "ok" : "ng"] += 1;
            visits[card.id] = {result: v.result, at: row.time};
            hist = [row].concat(hist);
        } else {                                        // the same part, read again
            if (vis.result !== v.result) {
                c[vis.result === "OK" ? "ok" : "ng"] -= 1;
                c[v.result === "OK" ? "ok" : "ng"] += 1;
                // a short NG that turned OK on the same part was not a real NG: put the
                // "last NG" panel back the way it was before this part came
                if (v.result === "OK" && vis.ngBefore !== undefined) {
                    flow.set("lastng", vis.ngBefore || null);
                    out[3] = {payload: vis.ngBefore || {}};
                }
                vis.result = v.result;
            }
            const i = hist.findIndex(h => h.roi === r.roi && h.time === vis.at);
            if (i >= 0) hist[i] = Object.assign(row, {time: vis.at});
            else hist = [row].concat(hist);
        }
        c.yield = Math.round(100 * c.ok / Math.max(1, c.ok + c.ng));
        hist = hist.slice(0, 15);
        out[1] = {payload: c};
        out[2] = {payload: hist};
        if (v.result === "NG") {
            const vv = visits[card.id];
            if (vv.ngBefore === undefined) vv.ngBefore = flow.get("lastng") || null;
            // the picture is sent 4 times a second - take the next one, after this reading
            const cam = r.camera, id = card.id, ng = {time, roi: r.roi, found};
            setTimeout(() => {
                const now = (flow.get("visits") || {})[id];
                if (!now || now.result !== "NG") return;          // already corrected to OK
                ng.pic = (flow.get("pics") || {})[cam] || null;
                flow.set("lastng", ng);
                node.send([null, null, null, {payload: ng}]);
            }, 300);
        }
        node.status({fill: v.result === "OK" ? "green" : "red", shape: "dot",
                     text: "OK " + c.ok + " · NG " + c.ng + " (now " + v.result + " " + found + "/" + v.expect + ")"});
    }
    flow.set("visits", visits);
    flow.set("qc", c);
    flow.set("hist", hist);
}
return out;
"""

RESET_FN = r"""// "Reset counts" on the dashboard: OK / NG, history and the last NG start again.
const c = {ok: 0, ng: 0, yield: 0, since: new Date().toLocaleString("en-GB")};
flow.set("qc", c);
flow.set("hist", []);
flow.set("visits", {});
flow.set("lastng", null);
node.status({fill: "grey", shape: "ring", text: "counts reset " + c.since});
return [{payload: c}, {payload: []}, {payload: {}}];
"""


def tmpl(id_, group, name, fmt, w, h, x, y, order=1):
    return node(id_, "ui_template", group=group, name=name, order=order, width=str(w), height=str(h),
                format=fmt, storeOutMessages=True, fwdInMessages=True, resendOnRefresh=True,
                templateScope="local", className="", x=x, y=y, wires=[[]])


def group(id_, name, order, w):
    return {"id": id_, "type": "ui_group", "name": name, "tab": "tv_ui_tab", "order": order,
            "disp": True, "width": str(w), "collapse": False, "className": ""}


flow = [
    {"id": Z, "type": "tab", "label": "TESR Vision Dashboard", "disabled": False, "info": INFO},
    node("tv_aedes", "aedes broker", name="the broker (port 1883)", mqtt_port="1883", mqtt_ws_bind="port",
         mqtt_ws_port="", mqtt_ws_path="", cert="", key="", certname="", keyname="", dburl="",
         usetls=False, x=150, y=60, wires=[["tv_who"], []]),
    node("tv_who", "debug", name="who connected", active=True, tosidebar=True, console=False,
         tostatus=False, complete="topic", targetType="msg", statusVal="", statusType="auto",
         x=400, y=60, wires=[]),
    node("tv_in_pic", "mqtt in", name="camera picture", topic="tesr/vision/+/frame", qos="0",
         datatype="utf8", broker=BROKER, nl=False, rh=0, inputs=0, x=150, y=140, wires=[["tv_fn_pic"]]),
    node("tv_fn_pic", "function", name="keep the picture", func=FRAME_FN, outputs=1, timeout=0, noerr=0,
         initialize="", finalize="", libs=[], x=370, y=140, wires=[["tv_ui_cam"]]),
    tmpl("tv_ui_cam", "tv_g_cam", "camera", CAMERA, 12, 10, 600, 140),
    node("tv_in_read", "mqtt in", name="every reading", topic="tesr/vision/+/+", qos="0",
         datatype="auto-detect", broker=BROKER, nl=False, rh=0, inputs=0, x=150, y=240, wires=[["tv_fn_read"]]),
    node("tv_fn_read", "function", name="reading -> dashboard", func=READING_FN, outputs=4, timeout=0, noerr=0,
         initialize="", finalize="", libs=[], x=380, y=240,
         wires=[["tv_ui_now"], ["tv_ui_count"], ["tv_ui_hist"], ["tv_ui_ng"]]),
    tmpl("tv_ui_now", "tv_g_now", "result now", NOW, 6, 4, 640, 200),
    tmpl("tv_ui_count", "tv_g_count", "OK / NG counts", COUNT, 6, 4, 640, 240),
    tmpl("tv_ui_hist", "tv_g_hist", "last 15", HIST, 6, 6, 630, 280),
    tmpl("tv_ui_ng", "tv_g_ng", "last NG", LAST_NG, 6, 6, 630, 320),
    node("tv_btn_reset", "ui_button", name="reset counts", group="tv_g_count", order=2, width=6, height=1,
         passthru=False, label="เริ่มนับใหม่ (Reset counts)", tooltip="", color="", bgcolor="", className="",
         icon="replay", payload="reset", payloadType="str", topic="reset", topicType="str",
         x=150, y=400, wires=[["tv_fn_reset"]]),
    node("tv_fn_reset", "function", name="zero the counts", func=RESET_FN, outputs=3, timeout=0, noerr=0,
         initialize="", finalize="", libs=[], x=370, y=400, wires=[["tv_ui_count"], ["tv_ui_hist"], ["tv_ui_ng"]]),
    {"id": BROKER, "type": "mqtt-broker", "name": "the aedes broker in this flow", "broker": "localhost",
     "port": "1883", "clientid": "", "autoConnect": True, "usetls": False, "protocolVersion": "4",
     "keepalive": "60", "cleansession": True, "birthTopic": "", "birthQos": "0", "birthPayload": "",
     "closeTopic": "", "closeQos": "0", "closePayload": "", "willTopic": "", "willQos": "0", "willPayload": ""},
    group("tv_g_cam", "กล้อง + AI (สด)", 1, 12),
    group("tv_g_now", "ผลตอนนี้", 2, 6),
    group("tv_g_count", "นับ OK / NG", 3, 6),
    group("tv_g_ng", "NG ล่าสุด", 4, 6),
    group("tv_g_hist", "ประวัติ 15 ครั้งล่าสุด", 5, 6),
    {"id": "tv_ui_tab", "type": "ui_tab", "name": "TESR Vision", "icon": "dashboard", "order": 1,
     "disabled": False, "hidden": False},
]

text = json.dumps(flow, ensure_ascii=False, indent=1)
# "</" -> "<\/" is the same JSON, and lets ROI Studio carry the file inside a <script> block
text = text.replace("</", "<\\/")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    f.write(text + "\n")
print("wrote %s (%d nodes)" % (os.path.relpath(OUT, ROOT), len(flow)))
