"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { spawnSync } = require("node:child_process");

const launcher = path.resolve(__dirname, "..", "bin", "recall.js");
const { engineAt, parseArgs, target } = require(launcher);

function run(args) {
  return spawnSync(process.execPath, [launcher, ...args], { encoding: "utf8" });
}

test("launcher installs and diagnoses an isolated target", () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "recall-launcher-"));
  try {
    const installed = run(["install", "--target", temporary]);
    assert.equal(installed.status, 0, installed.stderr);
    const engine = path.join(temporary, "skills", "recall", "scripts", "recall.py");
    assert.equal(fs.statSync(engine).mode & 0o111, 0o111);

    const diagnosed = run(["doctor", "--target", temporary]);
    assert.equal(diagnosed.status, 0, diagnosed.stdout + diagnosed.stderr);
    assert.match(diagnosed.stdout, /doctor: all good/);
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
  }
});

test("launcher reports usage and rejects an unknown command", () => {
  assert.equal(run([]).status, 0);
  const unknown = run(["not-a-command"]);
  assert.equal(unknown.status, 1);
  assert.match(unknown.stdout, /^usage:/);
});

test("argument and target resolution are deterministic", () => {
  const parsed = parseArgs(["doctor", "--target", "relative-target"]);
  assert.deepEqual(parsed._, ["doctor"]);
  assert.equal(parsed.target, "relative-target");
  assert.equal(target(parsed), path.resolve("relative-target"));
  assert.equal(
    engineAt(parsed),
    path.join(path.resolve("relative-target"), "skills", "recall", "scripts", "recall.py"),
  );
});
