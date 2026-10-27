/* Load index.html in a headless DOM and check the things that break silently:
   a JavaScript error at boot, a missing translation, a task without a label or
   a params panel, and the plate logic agreeing with the Python runner. */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const ROOT = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");

const fail = [];
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`${name.padEnd(56)} ${ok ? "ok " : "FAIL"}   got=${JSON.stringify(got)}`);
  if (!ok) fail.push(`${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
}

const errors = [];
const { VirtualConsole } = require("jsdom");
const dom = new JSDOM(html, {
  runScripts: "dangerously",
  pretendToBeVisual: true,
  url: "https://tesr-channel.github.io/tesr-roi-studio/",
  virtualConsole: new VirtualConsole()
    .on("jsdomError", e => errors.push(String(e.message)))
    .on("error", (...a) => errors.push(a.join(" "))),
  beforeParse(window) {
    // jsdom has no layout engine and no canvas: stub the two APIs the page uses
    // at boot so the rest of the script can be exercised.
    window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
    // a no-op 2D context: every drawing call is accepted and nothing is measured
    const ctx = new Proxy({}, {
      get(_, k) {
        if (k === "canvas") return { width: 1, height: 1 };
        if (k === "measureText") return () => ({ width: 10 });
        if (k === "getImageData") return (x, y, w, h) =>
          ({ data: new Uint8ClampedArray(Math.max(1, w * h * 4)), width: w, height: h });
        if (k === "createImageData") return (w, h) =>
          ({ data: new Uint8ClampedArray(Math.max(1, w * h * 4)), width: w, height: h });
        return () => undefined;
      },
      set() { return true; },
    });
    window.HTMLCanvasElement.prototype.getContext = () => ctx;
    if (window.HTMLDialogElement && !window.HTMLDialogElement.prototype.showModal) {
      window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
      window.HTMLDialogElement.prototype.close = function () { this.open = false; };
    }
  },
});
const win = dom.window;
// top-level const/let in a classic script are not window properties, so the
// page's own globals have to be read inside its own scope
const G = name => win.eval(name);

// Wait for the page's own boot to finish rather than guessing at a delay: a
// slow CI runner needs longer than a laptop, and a fixed timeout that is too
// short fails for the wrong reason.
function whenReady(done, waited = 0) {
  let ready = false;
  try { ready = typeof win.eval("I18N") === "object"; } catch (e) { ready = false; }
  if (ready && waited >= 300) return done();
  if (waited > 20000) {
    fail.push("the page did not finish booting within 20s");
    return done();
  }
  setTimeout(() => whenReady(done, waited + 100), 100);
}

whenReady(() => {
  console.log("--- boot ---");
  check("no uncaught error at boot",
        errors.filter(e => !/getContext|not implemented|Could not parse/i.test(e)), []);
  check("error banner stayed hidden", win.document.getElementById("errBar").style.display, "none");

  const I18N = G("I18N"), TASKS = G("TASKS");
  console.log("\n--- translations ---");
  const th = Object.keys(I18N.th), en = Object.keys(I18N.en);
  check("same number of keys in both languages", [th.length === en.length, th.length], [true, th.length]);
  check("no key only in Thai", th.filter(k => !(k in I18N.en)), []);
  check("no key only in English", en.filter(k => !(k in I18N.th)), []);
  check("no empty Thai string", th.filter(k => I18N.th[k] === ""), []);
  // dGroups3 is a Thai classifier that English does not need; the sentence it
  // joins reads correctly either way, so an empty string there is intentional
  const FRAGMENT = ["dGroups3"];
  check("no empty English string",
        en.filter(k => I18N.en[k] === "" && !FRAGMENT.includes(k)), []);
  check("no key empty in both languages",
        en.filter(k => I18N.en[k] === "" && I18N.th[k] === ""), []);

  console.log("\n--- every task is complete ---");
  const tasks = Object.keys(TASKS);
  check("task list", tasks.length, 13);
  check("plate task registered", tasks.includes("plate_ocr"), true);
  check("gauge task registered", tasks.includes("gauge_needle"), true);
  check("every task has a label in both languages",
        tasks.filter(k => !I18N.th["task_" + k] || !I18N.en["task_" + k]), []);
  check("every task has a hint in both languages",
        tasks.filter(k => !I18N.th["hint_" + k] || !I18N.en["hint_" + k]), []);
  check("every task has default params",
        tasks.filter(k => typeof G("defaultParams")(k) !== "object"), []);
  const opts = [...win.document.getElementById("newTask").options].map(o => o.value);
  check("every task is in the new-ROI dropdown", tasks.filter(k => !opts.includes(k)), []);

  console.log("\n--- plate format logic matches the runner ---");
  const pc = G("plateClean");
  const sq = { pattern: "^[A-Z]{2}[0-9]{4}$" };
  check("a plate that fits the format is kept", pc("AB1234", sq).plate, "AB1234");
  check("O in a digit slot becomes 0", pc("ABO234", sq).plate, "AB0234");
  check("0 in a letter slot becomes O", pc("A01234", sq).plate, "AO1234");
  check("no swap invented when none fits", pc("XY77", sq).plate, "");
  check("without a pattern nothing is swapped", pc("ABO234", {}).plate, "ABO234");
  check("non-plate characters dropped", pc("*AB-1234*", {}).plate, "AB1234");
  check("lower case normalised", pc("ab1234", {}).plate, "AB1234");
  check("too few characters withheld", pc("AB1", {}).plate, "");
  check("too many characters withheld", pc("AB123456789012", {}).plate, "");
  check("a broken pattern does not block the read",
        pc("AB1234", { pattern: "([unclosed" }).plate, "AB1234");
  check("a rejection carries a reason", pc("ABCDEF", sq).reason.length > 0, true);

  console.log("\n--- OCR language is picked, not typed (v1.8.2) ---");
  const nl = G("normLang");
  check("th is mapped to tha", nl("th"), { ok: true, code: "tha" });
  check("tha+eng stays as it is", nl("tha+eng"), { ok: true, code: "tha+eng" });
  check("not a language code is refused", nl("ไทย").ok, false);
  const pw = G("plateWhitelistFor"), thWL = G("PLATE_WL").tha;
  check("Thai plate letters: 0-9 + 44 consonants", thWL.length, 54);
  check("tha gives the Thai set", pw("tha"), thWL);
  check("eng gives A-Z 0-9", pw("eng"), G("PLATE_CHARS"));
  const uiWas = G("LANG");
  win.eval('LANG = "en"');
  check("a new plate on the English page starts on eng", G("defaultParams")("plate_ocr").lang, "eng");
  win.eval('LANG = "th"');
  const roi = { task: "plate_ocr", params: G("defaultParams")("plate_ocr") };
  win.eval("LANG = " + JSON.stringify(uiWas));
  check("a new plate on the Thai page starts on tha", [roi.params.lang, roi.params.whitelist], ["tha", thWL]);
  const s1 = G("langSel")(roi, true);
  check("the language is a list, not a text box", s1.tagName, "SELECT");
  check("the list offers eng, tha, tha+eng", [...s1.options].map(o => o.value), ["eng", "tha", "tha+eng"]);
  s1.value = "eng"; s1.onchange();
  check("switching to eng swaps a stock whitelist", roi.params.whitelist, G("PLATE_CHARS"));
  roi.params.whitelist = "0123456789ABC";
  const s2 = G("langSel")(roi, true); s2.value = "tha"; s2.onchange();
  check("an edited whitelist is left alone", roi.params.whitelist, "0123456789ABC");
  const odd = { task: "plate_ocr", params: Object.assign(G("defaultParams")("plate_ocr"), { lang: "th" }) };
  const s3 = G("langSel")(odd, true);
  check("an imported value off the list is kept", s3.value, "th");

  console.log("\n--- gauge calibration matches the runner ---");
  // gauge_fraction() in tesr_vision_runner.py, same cases, same answers: the
  // sweep direction has to come out of the two reference angles alone.
  const gf = G("gaugeFraction"), r3 = x => x === null ? null : +x.toFixed(3);
  check("clockwise 225 -> -45, needle at the low end", r3(gf(225, 225, -45)), 0);
  check("clockwise 225 -> -45, needle straight up", r3(gf(90, 225, -45)), 0.5);
  check("clockwise 225 -> -45, needle at the high end", r3(gf(315, 225, -45)), 1);
  check("anticlockwise -45 -> 225, needle straight up", r3(gf(90, -45, 225)), 0.5);
  check("anticlockwise -45 -> 225, needle at 135", r3(gf(135, -45, 225)), 0.667);
  check("dead sector below the hub is off the scale",
        gf(270, 225, -45) > 1 || gf(270, 225, -45) < 0, true);
  check("two identical reference angles are refused", gf(90, 90, 90), null);

  // the needle is several rays wide: the answer is the middle of the one run
  const ga = G("gaugeAngle"), STEP = G("GAUGE_STEP");
  const reach = win.eval("new Float64Array(" + Math.round(360 / STEP) + ")");
  for (let k = -3; k <= 3; k++) reach[Math.round(90 / STEP) + k] = 0.8;
  check("a needle seven rays wide reads as its middle",
        +ga(reach).theta.toFixed(2), 90);
  check("an empty dial has no needle", ga(new win.Float64Array(720)), null);

  console.log("\n--- counter reset settings ---");
  const cr = G("state").runtime.counter_reset;
  check("default is no reset", cr.mode, "none");
  check("reset fields present", Object.keys(cr).sort(),
        ["at", "minutes", "mode", "shifts", "state_file"]);
  const D = id => win.document.getElementById(id);
  check("reset controls exist",
        ["rtReset", "rtShifts", "rtResetAt", "rtResetMins", "rtStateFile"].filter(i => !D(i)), []);
  D("rtReset").value = "shift";
  D("rtShifts").value = "06:00, 14:00, 22:00";
  D("rtStateFile").value = "logs/counters_{camera}.json";
  D("rtClose").onclick();
  check("shift settings are saved", [cr.mode, cr.shifts, cr.state_file],
        ["shift", ["06:00", "14:00", "22:00"], "logs/counters_{camera}.json"]);
  const cfg = G("buildConfig")(false);
  check("reset settings reach the exported config",
        cfg.runtime.counter_reset.shifts, ["06:00", "14:00", "22:00"]);

  console.log("\n--- attaching a model keeps the region's own conf / iou ---");
  const attach = G("attachModel");
  const mdl = { id: "m1", deploy: "model.onnx", size: 224, conf: 0.25, iou: 0.45,
                names: ["a", "b"], _fileImgsz: 224 };
  const imported = attach({ path: "model.onnx", imgsz: 224, conf: 0.35,
                            class_names: ["a", "b"] }, mdl);
  check("imported conf 0.35 survives attach", [imported.conf, imported.iou], [0.35, 0.45]);
  const fresh = attach(G("defaultModel")("yolo_detect"), mdl);
  check("a fresh region takes the dialog values", [fresh.conf, fresh.iou, fresh.imgsz], [0.25, 0.45, 224]);
  const typed = attach({ conf: 0.6, iou: 0.3, imgsz: 640 }, mdl);
  check("the file's imgsz wins, typed conf/iou stay", [typed.conf, typed.iou, typed.imgsz], [0.6, 0.3, 224]);
  const blank = attach({ conf: "", path: "x" }, mdl);
  check("a cleared conf field takes the dialog value", blank.conf, 0.25);

  console.log("\n--- export text says the model is in the zip ---");
  const readme = G("projectReadme")("p_config.json", [mdl]);
  check("README.txt no longer says 'beside itself'", /beside itself/.test(readme), false);
  check("README.txt says the current folder", /folder you RUN the command in/.test(readme), true);
  const stale = /copy the \.onnx|คัดลอกไฟล์ \.onnx|ต้องคัดลอกไปวาง/;
  check("no dialog string tells you to copy the .onnx",
        Object.values(G("I18N")).flatMap(d => Object.values(d)).filter(v => stale.test(String(v))).length, 0);
  check("quickstart does not tell you to copy the .onnx",
        stale.test(G("quickstart")("p_config.json", "p_project.zip", [mdl], 1024)), false);

  console.log("\n--- classes are clicked, not typed (v1.8.0) ---");
  const cp = G("classPicker");
  let got = null;
  const names = ["Red_Apple", "Green_Apple", "Orange", "Lemon"];
  const box = cp(names, [], v => { got = v; }, "none");
  const chips = () => [...box.querySelectorAll(".chip.pick")];
  check("one chip per class plus All", chips().length, 5);
  check("no text box to type a class into", box.querySelectorAll("input").length, 0);
  chips().find(b => /Orange/.test(b.textContent)).click();
  check("clicking a class keeps only that class", got, ["Orange"]);
  chips().find(b => /Red_Apple/.test(b.textContent)).click();
  check("picks come back in the model's order", got, ["Red_Apple", "Orange"]);
  chips().find(b => /All classes/.test(b.textContent)).click();
  check("All classes clears the filter (= every class)", got, []);
  names.forEach(n => chips().find(b => b.textContent.endsWith(" " + n)).click());
  check("ticking every class is the same as All", got, []);
  const stale2 = cp(names, ["orenge"], v => { got = v; }, "none");
  check("a name the model lacks is shown as a warning chip",
        stale2.querySelectorAll(".chip.warn").length, 1);
  stale2.querySelector(".chip.warn").click();
  check("and one click removes it", got, []);
  check("no model picked says where to pick one",
        cp([], [], () => {}, "pick-a-model").textContent.includes("pick-a-model"), true);

  console.log("\n--- the browser counts a line the way the runner does ---");
  // a box moving left to right through a vertical gate drawn top to bottom:
  // the gold arrow of such a gate points left-to-right, so this is one "in"
  const gateRoi = {id: "g", task: "line_count", shape: "line", points: [[0.5, 0], [0.5, 1]],
                   params: {label_in: "in", label_out: "out", classes: []}};
  G("resetTally")();
  for (let x = 101; x <= 500; x += 37) {
    G("trackUpdate")([{box: [x - 20, 80, x + 20, 120], name: "Orange", conf: 0.9}], 600, 200);
    G("countLineJS")(gateRoi, 600, 200);
  }
  check("left to right across the gate is one in", G("countLineJS")(gateRoi, 600, 200), {i: 1, o: 0});
  gateRoi.params.invert = true; G("resetTally")();
  for (let x = 101; x <= 500; x += 37) {
    G("trackUpdate")([{box: [x - 20, 80, x + 20, 120], name: "Orange", conf: 0.9}], 600, 200);
    G("countLineJS")(gateRoi, 600, 200);
  }
  check("invert turns it into an out", G("countLineJS")(gateRoi, 600, 200), {i: 0, o: 1});
  gateRoi.params.invert = false; gateRoi.params.classes = ["Lemon"]; G("resetTally")();
  for (let x = 101; x <= 500; x += 37) {
    G("trackUpdate")([{box: [x - 20, 80, x + 20, 120], name: "Orange", conf: 0.9}], 600, 200);
    G("countLineJS")(gateRoi, 600, 200);
  }
  check("a class left out is not counted", G("countLineJS")(gateRoi, 600, 200), {i: 0, o: 0});

  console.log("\n--- the exported Python matches runner/ byte for byte ---");
  for (const [id, file] of [["runnerSrc", "runner/tesr_vision_runner.py"],
                            ["lineSrc", "runner/tesr_line_engine.py"]]) {
    const embedded = win.document.getElementById(id).textContent;
    const onDisk = fs.readFileSync(path.join(ROOT, file), "utf8");
    check(`${id} identical to ${file}`, embedded === onDisk, true);
  }
  check("exported runner carries the plate task",
        win.document.getElementById("runnerSrc").textContent.includes('"plate_ocr": task_plate'), true);
  check("exported runner carries the gauge task",
        win.document.getElementById("runnerSrc").textContent.includes('"gauge_needle": task_gauge'), true);
  check("exported engine carries the period clock",
        win.document.getElementById("lineSrc").textContent.includes("class PeriodClock"), true);

  importTests().then(finish, e => { fail.push("import tests threw: " + e.message); finish(); });
});

/* Export, then Import, must give back a project that runs and edits as it was:
   models attached, each camera's detector attached, nothing to add again. */
async function importTests() {
  console.log("\n--- import gives the project back, models attached ---");
  const enc = new win.TextEncoder();
  const models = G("models"), state = G("state");
  const fakeOnnx = n => new win.Uint8Array([8, 7, 18, 4, 116, 101, 115, 116, n]);   // not a real graph; only bytes
  const cfg = {
    schema: "tesr.roi.v1", studio_version: "1.8.6", project: "Line test",
    cameras: [{id: "cam1", name: "Camera 1", source: "0", width: 640, height: 480,
      detector: {path: "model.onnx", class_names: ["Red_Apple", "Green_Apple", "Orange", "Lemon"],
                 imgsz: 224, conf: 0.25, iou: 0.45},
      rois: [{id: "r1", name: "line_1", task: "line_count", shape: "line", points: [[0, 0.48], [1, 0.48]],
              params: {classes: ["Orange"], invert: true}},
             {id: "r2", name: "slot", task: "yolo_detect", rect: {x: 0.1, y: 0.1, w: 0.2, h: 0.2, angle: 0},
              model: {path: "model.onnx", imgsz: 224, class_names: ["Red_Apple", "Green_Apple", "Orange", "Lemon"]},
              params: {expect: "Orange"}}]}]};
  const reset = () => { models.splice(0, models.length); };
  const cfgBytes = enc.encode(JSON.stringify(cfg));
  const missing = () => win.document.getElementById("dlgMissing").open === true;
  const closeMissing = () => { win.document.getElementById("dlgMissing").open = false; };

  // 1. the unzipped folder, picked together: config + model, no manifest
  reset(); closeMissing();
  await G("openProjectFiles")(new win.Map([["line_test_config.json", cfgBytes], ["model.onnx", fakeOnnx(1)]]));
  const c1 = state.cameras[0];
  check("config + .onnx together: one model kept", models.map(m => m.deploy), ["model.onnx"]);
  check("... the camera's detector is attached", !!(c1.detector.id && c1.detector.id === models[0].id), true);
  check("... the model region is attached", c1.rois[1].model.id === models[0].id, true);
  check("... its classes come from the config", models[0].names, ["Red_Apple", "Green_Apple", "Orange", "Lemon"]);
  check("... imgsz from the config when the file has none", models[0].size, 224);
  check("... no 'missing model' dialog", missing(), false);
  check("... the line keeps its class filter", c1.rois[0].params.classes, ["Orange"]);

  // 2. a config.json alone, the model already in this browser: attached by file name
  await G("openProjectFiles")(new win.Map([["line_test_config.json", cfgBytes]]));
  const c2 = state.cameras[0];
  check("config alone, model already here: detector attached", c2.detector.id === models[0].id, true);
  check("... region attached", c2.rois[1].model.id === models[0].id, true);
  check("... still no dialog", missing(), false);
  check("... and Export would carry the model", G("modelsInUse")().map(m => m.deploy), ["model.onnx"]);

  // 3. a config alone, the model nowhere: ask for it by name, then attach it
  reset();
  await G("openProjectFiles")(new win.Map([["line_test_config.json", cfgBytes]]));
  check("config alone, no model: the dialog asks for it", missing(), true);
  check("... by its file name", win.document.getElementById("missList").textContent, "model.onnx");
  closeMissing();
  await G("attachModels")(new win.Map([["model.onnx", fakeOnnx(2)]]), ["model.onnx"]);
  check("... picking it attaches the detector", state.cameras[0].detector.id === models[0].id, true);
  check("... and the region", state.cameras[0].rois[1].model.id === models[0].id, true);

  // 4. the exported zip, read back: the round trip the button promises
  const built = G("buildConfig")(true);
  const manifest = {schema: "tesr.roi.project.v1", config: "line_test_config.json",
    models: G("modelsInUse")().map(m => ({id: m.id, name: m.name, type: m.type, size: m.size, conf: m.conf,
      iou: m.iou, names: m.names, deploy: m.deploy, file: m.deploy}))};
  check("export strips the browser id from the detector", "id" in built.cameras[0].detector, false);
  const zip = G("zipWrite")([{name: "line_test_config.json", data: enc.encode(JSON.stringify(built))},
                             {name: "model.onnx", data: models[0].bytes},
                             {name: "project.json", data: enc.encode(JSON.stringify(manifest))}]);
  const zbytes = await new Promise((res, rej) => {        // jsdom's Blob has no arrayBuffer()
    const fr = new win.FileReader();
    fr.onload = () => res(new win.Uint8Array(fr.result)); fr.onerror = rej;
    fr.readAsArrayBuffer(zip);
  });
  reset();
  await G("openProjectFiles")(await G("zipRead")(zbytes));
  check("exported zip back in: one model", models.map(m => m.deploy), ["model.onnx"]);
  check("... detector attached", state.cameras[0].detector.id === models[0].id, true);
  check("... region attached", state.cameras[0].rois[1].model.id === models[0].id, true);
  check("... line and its filter intact", [state.cameras[0].rois[0].task, state.cameras[0].rois[0].params.classes],
        ["line_count", ["Orange"]]);
  check("... no dialog", missing(), false);
}

function finish() {
  console.log();
  if (fail.length) {
    console.log(`${fail.length} FAILED`);
    fail.forEach(f => console.log("  - " + f));
    dom.window.close();
    process.exit(1);
  }
  console.log("all page checks passed");
  dom.window.close();          // the render loop would otherwise keep node alive
  process.exit(0);
}
