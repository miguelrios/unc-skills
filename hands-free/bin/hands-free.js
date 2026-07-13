#!/usr/bin/env node
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const root = path.resolve(__dirname, "..");
const args = process.argv.slice(2);
const command = args.shift() || "help";

function usage() {
  console.log(`Hands Free — phone-call bridge for AI coding assistants

Usage:
  hands-free install [--harness=claude-code|codex|pi]
  hands-free doctor  [--harness=claude-code|codex|pi]
  hands-free help

Defaults:
  --harness auto-detects (prefers ~/.claude, then ~/.codex, then ~/.pi/agent)

Environment:
  CLAUDE_HOME      Override ~/.claude
  CODEX_HOME       Override ~/.codex
  PI_CODING_AGENT_DIR  Override ~/.pi/agent
  HANDS_FREE_HOME  Override the per-harness <home>/hands-free directory
  PYTHON_BIN       Defaults to python3 found on PATH
`);
}

function parseFlag(name) {
  for (const arg of args) {
    if (arg.startsWith(`--${name}=`)) return arg.slice(name.length + 3);
    if (arg === `--${name}`) {
      const next = args[args.indexOf(arg) + 1];
      if (next && !next.startsWith("--")) return next;
    }
  }
  return undefined;
}

function detectHarness() {
  const explicit = parseFlag("harness") || process.env.HANDS_FREE_HARNESS;
  if (explicit) return explicit;
  if (fs.existsSync(path.join(process.env.CLAUDE_HOME || path.join(os.homedir(), ".claude")))) {
    return "claude-code";
  }
  if (fs.existsSync(path.join(process.env.CODEX_HOME || path.join(os.homedir(), ".codex")))) {
    return "codex";
  }
  if (fs.existsSync(process.env.PI_CODING_AGENT_DIR || path.join(os.homedir(), ".pi", "agent"))) {
    return "pi";
  }
  return "claude-code";
}

function harnessHome(harness) {
  if (harness === "codex") {
    return process.env.CODEX_HOME || path.join(os.homedir(), ".codex");
  }
  if (harness === "pi") {
    return process.env.PI_CODING_AGENT_DIR || path.join(os.homedir(), ".pi", "agent");
  }
  return process.env.CLAUDE_HOME || path.join(os.homedir(), ".claude");
}

function parseEnv(filePath) {
  if (!fs.existsSync(filePath)) {
    return {};
  }
  const env = {};
  for (const rawLine of fs.readFileSync(filePath, "utf8").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) {
      continue;
    }
    const index = line.indexOf("=");
    const key = line.slice(0, index).trim();
    const value = line.slice(index + 1).trim().replace(/^["']|["']$/g, "");
    env[key] = value;
  }
  return env;
}

function install() {
  if (process.platform === "win32") {
    console.error("hands-free install currently requires a POSIX shell.");
    process.exit(1);
  }
  const harness = detectHarness();
  const result = spawnSync("bash", [path.join(root, "install.sh"), `--harness=${harness}`], {
    cwd: root,
    env: process.env,
    stdio: "inherit",
  });
  process.exit(result.status === null ? 1 : result.status);
}

function doctor() {
  const harness = detectHarness();
  const home = harnessHome(harness);
  const callUser = path.join(home, "hands-free", "scripts", "call_user.py");
  const skillFile = path.join(home, "skills", "hands-free", "SKILL.md");
  const envFile = path.join(home, "hands-free", ".env");
  const configHome = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config");
  const portableEnvFile = path.join(configHome, "hands-free", ".env");
  const env = { ...parseEnv(portableEnvFile), ...parseEnv(envFile), ...process.env };
  const required = ["VAPI_API_KEY", "VAPI_PHONE_NUMBER_ID", "HANDS_FREE_PHONE_NUMBER"];

  const checks = [
    [fs.existsSync(callUser), `call script: ${callUser}`],
    [fs.existsSync(skillFile), `skill file: ${skillFile}`],
    [
      required.every((key) => env[key] && env[key] !== "+15555550123"),
      "required Vapi env values configured",
    ],
  ];

  // hands-free <= 0.2.x wired hooks that could dial on every tool call.
  // Re-running `install` removes them.
  const settingsFile = harness === "claude-code"
    ? path.join(home, "settings.json")
    : harness === "codex"
      ? path.join(home, "hooks.json")
      : null;
  const legacyWiring =
    settingsFile && fs.existsSync(settingsFile) && fs.readFileSync(settingsFile, "utf8").includes("hands-free");
  checks.push([!legacyWiring, "no legacy hook wiring (re-run install to clean up)"]);

  console.log(`harness: ${harness}`);
  let ok = true;
  for (const [passed, label] of checks) {
    console.log(`${passed ? "ok" : "missing"} ${label}`);
    ok = ok && passed;
  }
  process.exit(ok ? 0 : 1);
}

if (command === "install") {
  install();
} else if (command === "doctor") {
  doctor();
} else {
  usage();
}
