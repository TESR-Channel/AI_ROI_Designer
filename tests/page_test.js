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

setTimeout(() => {
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
  check("task list", tasks.length, 12);
  check("plate task registered", tasks.includes("plate_ocr"), true);
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

  console.log("\n--- the exported Python matches runner/ byte for byte ---");
  for (const [id, file] of [["runnerSrc", "runner/tesr_vision_runner.py"],
                            ["lineSrc", "runner/tesr_line_engine.py"]]) {
    const embedded = win.document.getElementById(id).textContent;
    const onDisk = fs.readFileSync(path.join(ROOT, file), "utf8");
    check(`${id} identical to ${file}`, embedded === onDisk, true);
  }
  check("exported runner carries the plate task",
        win.document.getElementById("runnerSrc").textContent.includes('"plate_ocr": task_plate'), true);
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
}, 1200);
