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

  console.log("\n--- classes are clicked, not typed (v1.7.6) ---");
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
});
