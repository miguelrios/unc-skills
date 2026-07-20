"use strict";

const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const readline = require("readline/promises");
const { spawnSync } = require("child_process");

const PACKAGE_ROOT = path.resolve(__dirname, "..");
const PARABLE_PY = path.join(
  PACKAGE_ROOT, "skills", "parable", "scripts", "parable.py",
);
const PATCH_PATH = path.join(
  PACKAGE_ROOT, "patches", "cliproxyapi-v7.2.88-claude-effort.patch",
);

const SETUP_SCHEMA_VERSION = 1;
const DEFAULT_PORT = 8317;
const PROXY_REPOSITORY = "https://github.com/router-for-me/CLIProxyAPI.git";
const PROXY_COMMIT = "93d74a890a44802f656d7f39a573916b2611896e";
const PROXY_PATCH_SHA256 = "d35b422da321265150fe393da80a686862ef642ee45c65a3e2fb908d689d5d1f";
const PROXY_BINARY_NAME = "parable-cliproxy-api";
const VENDOR_ORDER = Object.freeze(["chatgpt", "claude", "xai"]);
const VENDORS = Object.freeze({
  chatgpt: Object.freeze({
    models: Object.freeze(["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]),
  }),
  claude: Object.freeze({
    models: Object.freeze([
      "claude-sonnet-5",
      "claude-opus-4-8",
      "claude-haiku-4-5-20251001",
    ]),
  }),
  xai: Object.freeze({ models: Object.freeze(["grok-4.5"]) }),
});

class OnboardingError extends Error {}

class PromptSession {
  constructor(input, output) {
    this.output = output;
    this.interface = readline.createInterface({ input, output, terminal: Boolean(output.isTTY) });
    this.lines = this.interface[Symbol.asyncIterator]();
  }

  async ask(question) {
    this.output.write(question);
    const next = await this.lines.next();
    if (next.done) throw new OnboardingError("interactive input ended before setup was complete");
    return next.value;
  }

  close() {
    this.interface.close();
  }
}

function modeOf(stat) {
  return stat.mode & 0o777;
}

function octal(mode) {
  return mode.toString(8).padStart(4, "0");
}

function lstatOrNull(target) {
  try {
    return fs.lstatSync(target);
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

function requirePrivateDirectory(target, label) {
  const stat = lstatOrNull(target);
  if (!stat) throw new OnboardingError(`${label} is missing: ${target}`);
  if (stat.isSymbolicLink() || !stat.isDirectory()) {
    throw new OnboardingError(`${label} must be a real directory, not a symlink: ${target}`);
  }
  if (modeOf(stat) !== 0o700) {
    throw new OnboardingError(
      `${label} must have mode 0700 (found ${octal(modeOf(stat))}): ${target}`,
    );
  }
}

function createPrivateDirectory(target, label) {
  if (lstatOrNull(target)) {
    requirePrivateDirectory(target, label);
    return false;
  }
  fs.mkdirSync(target, { recursive: true, mode: 0o700 });
  fs.chmodSync(target, 0o700);
  requirePrivateDirectory(target, label);
  return true;
}

function requirePrivateFile(target, label) {
  const stat = lstatOrNull(target);
  if (!stat) throw new OnboardingError(`${label} is missing: ${target}`);
  if (stat.isSymbolicLink() || !stat.isFile()) {
    throw new OnboardingError(`${label} must be a real regular file, not a symlink: ${target}`);
  }
  if (modeOf(stat) !== 0o600) {
    throw new OnboardingError(
      `${label} must have mode 0600 (found ${octal(modeOf(stat))}): ${target}`,
    );
  }
}

function parseOptions(argv, valueNames, booleanNames) {
  const result = { _: [] };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (booleanNames.has(arg)) {
      result[arg.slice(2).replaceAll("-", "_")] = true;
      continue;
    }
    if (valueNames.has(arg)) {
      if (index + 1 >= argv.length) {
        throw new OnboardingError(`${arg} requires a value`);
      }
      result[arg.slice(2).replaceAll("-", "_")] = argv[index + 1];
      index += 1;
      continue;
    }
    if (arg.startsWith("-")) throw new OnboardingError(`unknown option: ${arg}`);
    result._.push(arg);
  }
  return result;
}

function parseSetupOptions(argv) {
  const options = parseOptions(
    argv,
    new Set(["--vendors", "--proxy-bin", "--config-dir", "--port"]),
    new Set(["--build-proxy", "--no-auth", "--non-interactive", "--help"]),
  );
  if (options._.length) {
    throw new OnboardingError(`unexpected setup argument: ${options._[0]}`);
  }
  if (options.proxy_bin && options.build_proxy) {
    throw new OnboardingError("--proxy-bin and --build-proxy are mutually exclusive");
  }
  if (options.port !== undefined) {
    if (!/^[0-9]+$/.test(options.port)) {
      throw new OnboardingError("--port must be an integer from 1 through 65535");
    }
    options.port = Number(options.port);
    if (options.port < 1 || options.port > 65535) {
      throw new OnboardingError("--port must be an integer from 1 through 65535");
    }
  }
  return options;
}

function parseBuildOptions(argv) {
  const options = parseOptions(
    argv,
    new Set(["--install-dir"]),
    new Set(["--help"]),
  );
  if (options._.length) {
    throw new OnboardingError(`unexpected proxy build argument: ${options._[0]}`);
  }
  return options;
}

function setupUsage() {
  return [
    "usage: parable setup [--vendors chatgpt[,claude][,xai]] [--proxy-bin PATH]",
    "                     [--config-dir DIR] [--port PORT] [--build-proxy]",
    "                     [--no-auth] [--non-interactive]",
    "",
    "Create a private, loopback-only Parable + CLIProxyAPI configuration.",
    "ChatGPT is mandatory because the parent model is exact gpt-5.6-sol.",
  ].join("\n");
}

function proxyBuildUsage() {
  return [
    "usage: parable proxy build [--install-dir DIR]",
    "",
    "Build the pinned, patched CLIProxyAPI in a new managed directory.",
  ].join("\n");
}

function parseVendors(raw) {
  const parts = String(raw).split(",").map((value) => value.trim()).filter(Boolean);
  if (!parts.length) throw new OnboardingError("--vendors cannot be empty");
  const unique = new Set();
  for (const vendor of parts) {
    if (!Object.hasOwn(VENDORS, vendor)) {
      throw new OnboardingError(
        `unsupported vendor '${vendor}' (supported: ${VENDOR_ORDER.join(", ")})`,
      );
    }
    if (unique.has(vendor)) throw new OnboardingError(`duplicate vendor '${vendor}'`);
    unique.add(vendor);
  }
  if (!unique.has("chatgpt")) {
    throw new OnboardingError("--vendors must include chatgpt for the gpt-5.6-sol parent");
  }
  return VENDOR_ORDER.filter((vendor) => unique.has(vendor));
}

async function askYesNo(prompt, question) {
  const answer = (await prompt.ask(`${question} [y/N] `)).trim().toLowerCase();
  return answer === "y" || answer === "yes";
}

async function selectVendors(options, existingManifest, prompt) {
  if (options.vendors !== undefined) return parseVendors(options.vendors);
  if (options.non_interactive) {
    throw new OnboardingError("--non-interactive requires --vendors including chatgpt");
  }
  if (existingManifest) return existingManifest.vendors;
  const vendors = ["chatgpt"];
  if (await askYesNo(prompt, "Add Claude subscription models?")) vendors.push("claude");
  if (await askYesNo(prompt, "Add xAI Grok 4.5 subscription?")) vendors.push("xai");
  return vendors;
}

function executableFromCandidate(candidate) {
  if (!candidate) return null;
  const candidates = [];
  if (candidate.includes(path.sep) || path.isAbsolute(candidate)) {
    candidates.push(path.resolve(candidate));
  } else {
    for (const directory of (process.env.PATH || "").split(path.delimiter)) {
      if (directory) candidates.push(path.join(directory, candidate));
    }
  }
  for (const possible of candidates) {
    try {
      fs.accessSync(possible, fs.constants.X_OK);
      const canonical = fs.realpathSync(possible);
      if (fs.statSync(canonical).isFile()) return canonical;
    } catch {
      // Continue to the next PATH candidate.
    }
  }
  return null;
}

function requireExecutable(candidate, source) {
  const executable = executableFromCandidate(candidate);
  if (!executable) throw new OnboardingError(`${source} is not an executable file: ${candidate}`);
  return executable;
}

function discoverProxy(options) {
  if (options.proxy_bin) return requireExecutable(options.proxy_bin, "--proxy-bin");
  if (process.env.PARABLE_CLIPROXY_BIN) {
    return requireExecutable(
      process.env.PARABLE_CLIPROXY_BIN,
      "PARABLE_CLIPROXY_BIN",
    );
  }
  for (const command of ["parable-cliproxy-api", "cli-proxy-api"]) {
    const executable = executableFromCandidate(command);
    if (executable) return executable;
  }
  return null;
}

function setupPaths(configDir) {
  return {
    proxyConfig: path.join(configDir, "cliproxy.yaml"),
    proxyEnv: path.join(configDir, "cliproxy.env"),
    parableConfig: path.join(configDir, "parable.toml"),
    manifest: path.join(configDir, "setup.json"),
  };
}

function stateOf(paths) {
  const present = Object.entries(paths).filter(([, target]) => lstatOrNull(target));
  if (present.length === 0) return "empty";
  if (present.length !== Object.keys(paths).length) {
    const names = present.map(([name]) => name).join(", ");
    throw new OnboardingError(
      `partial setup state found (${names}); move or remove it before rerunning setup`,
    );
  }
  return "complete";
}

function yamlString(value) {
  return JSON.stringify(value);
}

function renderProxyYaml(port, authDir, token) {
  return [
    'host: "127.0.0.1"',
    `port: ${port}`,
    `auth-dir: ${yamlString(authDir)}`,
    "api-keys:",
    `  - "${token}"`,
    "debug: false",
    "",
  ].join("\n");
}

function renderProxyEnv(token) {
  return `export CLIPROXY_API_KEY='${token}'\n`;
}

function executorBlock(id, model, tags, useFor, avoidFor = "Reviewing its own diff.") {
  return [
    `[executors.${id}]`,
    'provider = "claude"',
    `model = ${JSON.stringify(model)}`,
    `tags = [${tags.map((tag) => JSON.stringify(tag)).join(", ")}]`,
    `use_for = ${JSON.stringify(useFor)}`,
    `avoid_for = ${JSON.stringify(avoidFor)}`,
    "",
  ];
}

function renderParableToml(port, vendors) {
  const selected = new Set(vendors);
  const reviewer = selected.has("claude") ? "opus_exact" : "luna";
  const lines = [
    "# Generated by `parable setup`. Contains no provider OAuth credentials.",
    "[parable]",
    "version = 1",
    'default_executor = "terra"',
    `default_reviewer = ${JSON.stringify(reviewer)}`,
    "",
    "[claude]",
    `base_url = "http://127.0.0.1:${port}"`,
    'auth_token_env = "CLIPROXY_API_KEY"',
    'brain_model = "gpt-5.6-sol"',
    "",
    "[providers.claude]",
    'type = "subagent"',
    "",
    "# Disable Parable's alias-based built-ins; this setup routes exact ids only.",
    "[executors.sonnet]",
    "enabled = false",
    "",
    "[executors.opus]",
    "enabled = false",
    "",
    ...executorBlock(
      "terra", "gpt-5.6-terra", ["implementer", "subscription"],
      "Independent GPT implementation or debugging.",
    ),
    ...executorBlock(
      "luna", "gpt-5.6-luna", ["reviewer", "subscription"],
      "Independent GPT review or a second implementation.",
    ),
  ];

  if (selected.has("claude")) {
    lines.push(
      ...executorBlock(
        "sonnet_exact", "claude-sonnet-5", ["implementer", "subscription"],
        "Implementation through the exact entitled Sonnet model.",
      ),
      ...executorBlock(
        "opus_exact", "claude-opus-4-8", ["reviewer", "subscription"],
        "Deep review through the exact entitled Opus model.",
      ),
      ...executorBlock(
        "haiku_exact", "claude-haiku-4-5-20251001", ["mechanical", "subscription"],
        "Fast mechanical work through the exact entitled Haiku model.",
      ),
    );
  }
  if (selected.has("xai")) {
    lines.push(
      ...executorBlock(
        "grok", "grok-4.5", ["implementer", "third-family", "subscription"],
        "Independent implementation or adversarial review through the xAI subscription.",
      ),
    );
  }

  const mechanical = selected.has("claude")
    ? ["haiku_exact", "luna", "sonnet_exact"] : ["luna", "terra"];
  const feature = ["terra"];
  if (selected.has("claude")) feature.push("sonnet_exact");
  if (selected.has("xai")) feature.push("grok");
  const review = [reviewer, "luna"];
  if (selected.has("xai")) review.push("grok");
  const gnarly = selected.has("claude") ? ["opus_exact"] : ["luna"];
  lines.push(
    "[routing]",
    `mechanical = ${JSON.stringify(mechanical)}`,
    `feature = ${JSON.stringify(feature)}`,
    `refactor_wide = ${JSON.stringify(feature)}`,
    `gnarly = ${JSON.stringify(gnarly)}`,
    `review = ${JSON.stringify(review)}`,
    `smoke_test = ${JSON.stringify(gnarly)}`,
    `escalation = ${JSON.stringify([...new Set([...feature, ...gnarly])])}`,
    "",
  );
  return lines.join("\n");
}

function manifestFor(configDir, authDir, paths, vendors, proxyBinary, port) {
  return {
    schemaVersion: SETUP_SCHEMA_VERSION,
    vendors,
    proxyBinary,
    port,
    configDir,
    authDir,
    generatedPaths: {
      cliproxyYaml: paths.proxyConfig,
      cliproxyEnv: paths.proxyEnv,
      parableToml: paths.parableConfig,
      setupJson: paths.manifest,
    },
  };
}

function renderManifest(manifest) {
  return `${JSON.stringify(manifest, null, 2)}\n`;
}

function readManifest(paths) {
  try {
    return JSON.parse(fs.readFileSync(paths.manifest, "utf8"));
  } catch (error) {
    throw new OnboardingError(`setup.json is invalid: ${error.message}`);
  }
}

function equalJson(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function readExistingToken(paths) {
  const value = fs.readFileSync(paths.proxyEnv, "utf8");
  const match = /^export CLIPROXY_API_KEY='([0-9a-f]{64})'\n$/.exec(value);
  if (!match) throw new OnboardingError("cliproxy.env is not a valid generated token file");
  return match[1];
}

function validateExistingSetup(configDir, authDir, paths, desired) {
  requirePrivateDirectory(configDir, "configuration directory");
  requirePrivateDirectory(authDir, "CLIProxyAPI auth directory");
  for (const [name, target] of Object.entries(paths)) requirePrivateFile(target, name);

  const actualManifest = readManifest(paths);
  if (!equalJson(actualManifest, desired)) {
    throw new OnboardingError(
      "existing setup does not match the requested vendors, proxy binary, port, or paths",
    );
  }
  const token = readExistingToken(paths);
  const expected = new Map([
    [paths.proxyConfig, renderProxyYaml(desired.port, authDir, token)],
    [paths.proxyEnv, renderProxyEnv(token)],
    [paths.parableConfig, renderParableToml(desired.port, desired.vendors)],
    [paths.manifest, renderManifest(desired)],
  ]);
  for (const [target, content] of expected) {
    if (fs.readFileSync(target, "utf8") !== content) {
      throw new OnboardingError(`generated setup file has changed; refusing to overwrite: ${target}`);
    }
  }
  requireExecutable(desired.proxyBinary, "configured proxy binary");
}

function writePrivateFileSet(entries) {
  const temporary = [];
  const created = [];
  try {
    for (const [target, content] of entries) {
      const temp = path.join(
        path.dirname(target),
        `.${path.basename(target)}.${process.pid}.${crypto.randomBytes(8).toString("hex")}.tmp`,
      );
      const descriptor = fs.openSync(temp, "wx", 0o600);
      try {
        fs.writeFileSync(descriptor, content, "utf8");
        fs.fsyncSync(descriptor);
      } finally {
        fs.closeSync(descriptor);
      }
      fs.chmodSync(temp, 0o600);
      temporary.push(temp);
    }
    entries.forEach(([target], index) => {
      fs.linkSync(temporary[index], target);
      created.push(target);
      fs.unlinkSync(temporary[index]);
    });
  } catch (error) {
    for (const target of created.reverse()) {
      try { fs.unlinkSync(target); } catch { /* best-effort rollback */ }
    }
    throw error;
  } finally {
    for (const temp of temporary) {
      try { fs.unlinkSync(temp); } catch { /* already linked or best-effort cleanup */ }
    }
  }
}

function validateParableConfig(configPath, configDir) {
  const validationHome = path.join(configDir, ".validation-home-does-not-exist");
  const result = spawnSync(
    "python3",
    [PARABLE_PY, "config", "--validate"],
    {
      cwd: configDir,
      env: {
        ...process.env,
        HOME: validationHome,
        PARABLE_CONFIG: configPath,
      },
      encoding: "utf8",
    },
  );
  if (result.error) {
    throw new OnboardingError(`could not validate generated Parable config: ${result.error.message}`);
  }
  if (result.status !== 0) {
    const detail = (result.stderr || result.stdout || "unknown validation error").trim();
    throw new OnboardingError(`generated Parable config failed validation: ${detail}`);
  }
}

function commandPath(command) {
  return executableFromCandidate(command);
}

function runChecked(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd,
    env: process.env,
    stdio: options.capture ? ["ignore", "pipe", "pipe"] : "inherit",
    encoding: options.capture ? "utf8" : undefined,
  });
  if (result.error) throw new OnboardingError(`${command} could not start: ${result.error.message}`);
  if (result.status !== 0) {
    throw new OnboardingError(`${command} failed with exit status ${result.status}`);
  }
  return result;
}

function defaultBuildDirectory() {
  const dataHome = process.env.XDG_DATA_HOME
    ? path.resolve(process.env.XDG_DATA_HOME)
    : path.join(os.homedir(), ".local", "share");
  return path.join(dataHome, "parable", "cliproxyapi", PROXY_COMMIT);
}

function ensureBuildParent(destination, isDefault) {
  const parent = path.dirname(destination);
  if (isDefault) {
    const managedRoot = path.join(
      process.env.XDG_DATA_HOME ? path.resolve(process.env.XDG_DATA_HOME) : path.join(os.homedir(), ".local", "share"),
      "parable",
    );
    for (const directory of [managedRoot, path.join(managedRoot, "cliproxyapi")]) {
      createPrivateDirectory(directory, "managed build directory");
    }
  } else {
    fs.mkdirSync(parent, { recursive: true, mode: 0o700 });
  }
}

function buildProxy(options, log) {
  const digest = crypto.createHash("sha256").update(fs.readFileSync(PATCH_PATH)).digest("hex");
  if (digest !== PROXY_PATCH_SHA256) {
    throw new OnboardingError(
      `vendored CLIProxyAPI patch checksum mismatch (expected ${PROXY_PATCH_SHA256})`,
    );
  }
  const git = commandPath("git");
  const go = commandPath("go");
  if (!git) throw new OnboardingError("git is required to build CLIProxyAPI");
  if (!go) throw new OnboardingError("go is required to build CLIProxyAPI");

  const isDefault = !options.install_dir;
  const destination = options.install_dir
    ? path.resolve(options.install_dir) : defaultBuildDirectory();
  if (lstatOrNull(destination)) {
    throw new OnboardingError(`build destination already exists; refusing to mutate it: ${destination}`);
  }
  ensureBuildParent(destination, isDefault);
  let ownedDestination = false;
  try {
    ownedDestination = true;
    runChecked(git, ["clone", "--no-checkout", PROXY_REPOSITORY, destination]);
    fs.chmodSync(destination, 0o700);
    runChecked(git, ["-C", destination, "checkout", "--detach", PROXY_COMMIT]);
    const revision = runChecked(
      git, ["-C", destination, "rev-parse", "HEAD"], { capture: true },
    ).stdout.trim();
    if (revision !== PROXY_COMMIT) {
      throw new OnboardingError(
        `CLIProxyAPI source pin mismatch (expected ${PROXY_COMMIT}, found ${revision || "empty"})`,
      );
    }
    runChecked(git, [
      "-C", destination,
      "-c", "user.name=Parable managed build",
      "-c", "user.email=parable-build@invalid.example",
      "am", PATCH_PATH,
    ]);
    runChecked(go, [
      "test", "-count=1",
      "./internal/thinking",
      "./internal/translator/codex/claude",
    ], { cwd: destination });
    runChecked(go, [
      "test", "-count=1", "./test",
      "-run", "^TestThinkingE2EClaudeAdaptive_Body$",
    ], { cwd: destination });
    const binary = path.join(destination, PROXY_BINARY_NAME);
    runChecked(go, ["build", "-o", binary, "./cmd/server"], { cwd: destination });
    const binaryStat = lstatOrNull(binary);
    if (!binaryStat || binaryStat.isSymbolicLink() || !binaryStat.isFile()) {
      throw new OnboardingError("go build completed without producing the managed proxy binary");
    }
    fs.chmodSync(binary, 0o700);
    log(`built pinned CLIProxyAPI -> ${binary}`);
    return binary;
  } catch (error) {
    if (ownedDestination) fs.rmSync(destination, { recursive: true, force: true });
    throw error;
  }
}

async function runProxyBuild(argv, log) {
  const options = parseBuildOptions(argv);
  if (options.help) {
    log(proxyBuildUsage());
    return;
  }
  buildProxy(options, log);
}

function existingManifestSkeleton(configDir, paths) {
  if (stateOf(paths) !== "complete") return null;
  requirePrivateDirectory(configDir, "configuration directory");
  for (const [name, target] of Object.entries(paths)) requirePrivateFile(target, name);
  const manifest = readManifest(paths);
  if (
    manifest.schemaVersion !== SETUP_SCHEMA_VERSION
    || !Array.isArray(manifest.vendors)
    || typeof manifest.proxyBinary !== "string"
    || !Number.isInteger(manifest.port)
  ) {
    throw new OnboardingError("setup.json does not describe a supported complete setup");
  }
  return manifest;
}

async function runSetup(argv, log) {
  const options = parseSetupOptions(argv);
  if (options.help) {
    log(setupUsage());
    return;
  }
  const configDir = path.resolve(options.config_dir || path.join(os.homedir(), ".config", "parable"));
  const authDir = path.resolve(path.join(os.homedir(), ".cli-proxy-api"));
  const paths = setupPaths(configDir);
  const initialState = stateOf(paths);
  const existing = initialState === "complete" ? existingManifestSkeleton(configDir, paths) : null;
  let prompt = null;
  if (!options.non_interactive && (!existing || options.vendors === undefined)) {
    prompt = new PromptSession(process.stdin, process.stdout);
  }
  try {
    const vendors = await selectVendors(options, existing, prompt);
    const port = options.port ?? existing?.port ?? DEFAULT_PORT;
    let proxyBinary;
    if (existing && !options.proxy_bin && !process.env.PARABLE_CLIPROXY_BIN) {
      proxyBinary = existing.proxyBinary;
    } else if (options.build_proxy) {
      if (existing) proxyBinary = existing.proxyBinary;
      else proxyBinary = buildProxy({}, log);
    } else {
      proxyBinary = discoverProxy(options);
      if (!proxyBinary && !options.non_interactive) {
        const approved = await askYesNo(
          prompt,
          `No CLIProxyAPI binary was found. Build pinned commit ${PROXY_COMMIT.slice(0, 12)} now?`,
        );
        if (approved) proxyBinary = buildProxy({}, log);
      }
      if (!proxyBinary) {
        throw new OnboardingError(
          "CLIProxyAPI was not found; use --proxy-bin, PARABLE_CLIPROXY_BIN, PATH, or --build-proxy",
        );
      }
    }
    proxyBinary = requireExecutable(proxyBinary, "selected proxy binary");
    const desired = manifestFor(configDir, authDir, paths, vendors, proxyBinary, port);

    if (existing) {
      validateExistingSetup(configDir, authDir, paths, desired);
      validateParableConfig(paths.parableConfig, configDir);
      log(`setup is valid and unchanged -> ${configDir}`);
      return;
    }

    const configCreated = createPrivateDirectory(configDir, "configuration directory");
    const authCreated = createPrivateDirectory(authDir, "CLIProxyAPI auth directory");
    const token = crypto.randomBytes(32).toString("hex");
    const entries = [
      [paths.proxyConfig, renderProxyYaml(port, authDir, token)],
      [paths.proxyEnv, renderProxyEnv(token)],
      [paths.parableConfig, renderParableToml(port, vendors)],
      [paths.manifest, renderManifest(desired)],
    ];
    try {
      writePrivateFileSet(entries);
      validateParableConfig(paths.parableConfig, configDir);
    } catch (error) {
      for (const [target] of entries) {
        try { fs.unlinkSync(target); } catch { /* best-effort rollback */ }
      }
      if (authCreated) {
        try { fs.rmdirSync(authDir); } catch { /* keep non-empty auth state */ }
      }
      if (configCreated) {
        try { fs.rmdirSync(configDir); } catch { /* keep unexpected state for inspection */ }
      }
      throw error;
    }
    log(`created private setup -> ${configDir}`);
    log(`selected vendors: ${vendors.join(", ")}`);
    log("next: authorize each selected subscription, then start the local proxy");
  } finally {
    if (prompt) prompt.close();
  }
}

module.exports = {
  OnboardingError,
  PROXY_COMMIT,
  PROXY_PATCH_SHA256,
  VENDORS,
  runProxyBuild,
  runSetup,
  setupUsage,
  proxyBuildUsage,
};
