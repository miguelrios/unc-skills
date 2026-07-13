#!/usr/bin/env node
const { spawnSync } = require("node:child_process");
const os = require("node:os");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const args = process.argv.slice(2);
const command = args.shift() || "doctor";
const home = process.env.HOME || os.homedir();
let executable;
let commandArgs;

if (command === "install") {
  executable = path.join(root, "install.sh");
  commandArgs = args;
} else if (command === "setup") {
  const installArgs = args.filter(arg => arg.startsWith("--harness=") || ["--both", "--codex", "--claude-code"].includes(arg));
  const setupArgs = args.filter(arg => !installArgs.includes(arg));
  const installed = spawnSync(path.join(root, "install.sh"), installArgs, { stdio: "inherit", env: process.env });
  if (installed.error || installed.status !== 0) {
    process.exit(installed.status ?? 1);
  }
  const dataHome = process.env.XDG_DATA_HOME || path.join(home, ".local", "share");
  executable = process.env.PYTHON_BIN || "python3";
  commandArgs = [path.join(dataHome, "tether", "tether_notify.py"), "setup", ...setupArgs];
} else if (command === "doctor") {
  const dataHome = process.env.XDG_DATA_HOME || path.join(home, ".local", "share");
  executable = process.env.PYTHON_BIN || "python3";
  commandArgs = [path.join(dataHome, "tether", "tether_notify.py"), "doctor", ...args];
} else {
  process.stderr.write("usage: tether <setup|install|doctor> [options]\n");
  process.exit(2);
}

const result = spawnSync(executable, commandArgs, { stdio: "inherit", env: process.env });
if (result.error) {
  process.stderr.write(`${result.error.message}\n`);
  process.exit(1);
}
process.exit(result.status ?? 1);
