#!/usr/bin/env node
/* recall installer/doctor. Node 18+, no dependencies. */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const ROOT = path.resolve(__dirname, "..");
const SOURCE = path.join(ROOT, "skills", "recall");

function log(message) { process.stdout.write(message + "\n"); }
function fail(message) { process.stderr.write("error: " + message + "\n"); process.exit(1); }

function parseArgs(argv) {
  const args = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--project") args.project = true;
    else if (argv[i] === "--target") args.target = argv[++i];
    else args._.push(argv[i]);
  }
  return args;
}

function target(args) {
  if (args.target) return path.resolve(args.target);
  if (args.project) return path.join(process.cwd(), ".claude");
  return path.join(os.homedir(), ".claude");
}

function engineAt(args) {
  return path.join(target(args), "skills", "recall", "scripts", "recall.py");
}

function install(args) {
  if (!fs.existsSync(SOURCE)) fail("package is missing skills/recall — reinstall @parcha/recall");
  const dest = path.join(target(args), "skills", "recall");
  try {
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.rmSync(dest, { recursive: true, force: true });
    fs.cpSync(SOURCE, dest, { recursive: true });
    for (const file of ["recall.py", "recall-hook.sh"]) {
      const filePath = path.join(dest, "scripts", file);
      if (fs.existsSync(filePath)) fs.chmodSync(filePath, 0o755);
    }
    log("installed skill -> " + dest);
  } catch (error) {
    fail("install failed at " + dest + ": " + error.message);
  }
}

function doctor(args) {
  let ok = true;
  const py = spawnSync("python3", ["-c", [
    "import sqlite3, sys",
    "assert sys.version_info >= (3, 10)",
    "c = sqlite3.connect(':memory:')",
    "c.execute('CREATE VIRTUAL TABLE probe USING fts5(body)')",
    "print('python %d.%d; sqlite FTS5 ok' % sys.version_info[:2])"
  ].join("; ")], { encoding: "utf8" });
  if (py.status === 0) log("ok   " + py.stdout.trim());
  else { ok = false; log("FAIL python3 >= 3.10 with sqlite FTS5: " + (py.stderr || py.stdout || "not found").trim()); }

  const engine = engineAt(args);
  if (fs.existsSync(engine)) log("ok   engine -> " + engine);
  else { ok = false; log("FAIL engine missing -> " + engine); }
  log(ok ? "doctor: all good" : "doctor: problems found (see above)");
  process.exit(ok ? 0 : 1);
}

function main(argv = process.argv.slice(2)) {
  const args = parseArgs(argv);
  if (args._[0] === "install") install(args);
  else if (args._[0] === "doctor") doctor(args);
  else {
    log("usage: npx @parcha/recall <install|doctor> [--project] [--target DIR]");
    log("  install            copy the skill to ~/.claude/skills (or ./.claude/skills with --project)");
    log("  doctor             check python, sqlite FTS5, and the installed engine");
    process.exit(args._[0] ? 1 : 0);
  }
}

if (require.main === module) main();

module.exports = { engineAt, main, parseArgs, target };
