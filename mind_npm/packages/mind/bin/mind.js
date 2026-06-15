#!/usr/bin/env node

import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import readline from "node:readline/promises";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const packageRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const packageJson = JSON.parse(readFileSync(path.join(packageRoot, "package.json"), "utf8"));
const packageName = packageJson.name;
const currentVersion = packageJson.version;
const updateCheckIntervalMs = 24 * 60 * 60 * 1000;

function updateStateDir() {
  if (process.platform === "win32") {
    return path.join(process.env.APPDATA || os.homedir(), "Mind");
  }

  if (process.platform === "darwin") {
    return path.join(os.homedir(), "Library", "Application Support", "Mind");
  }

  return path.join(process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config"), "mind");
}

function updateStatePath() {
  return path.join(updateStateDir(), "update-check.json");
}

function loadUpdateState() {
  try {
    return JSON.parse(readFileSync(updateStatePath(), "utf8"));
  } catch {
    return {};
  }
}

function saveUpdateState(state) {
  try {
    mkdirSync(updateStateDir(), { recursive: true });
    writeFileSync(updateStatePath(), JSON.stringify(state, null, 2), "utf8");
  } catch {
    // Update state is best-effort and should never block application startup.
  }
}

function normalizeVersion(version) {
  return String(version || "")
    .trim()
    .replace(/^v/i, "")
    .split("-")[0]
    .split("+")[0];
}

function compareVersions(left, right) {
  const leftParts = normalizeVersion(left).split(".").map((part) => Number.parseInt(part, 10) || 0);
  const rightParts = normalizeVersion(right).split(".").map((part) => Number.parseInt(part, 10) || 0);
  const length = Math.max(leftParts.length, rightParts.length);

  for (let index = 0; index < length; index += 1) {
    const leftValue = leftParts[index] || 0;
    const rightValue = rightParts[index] || 0;
    if (leftValue > rightValue) return 1;
    if (leftValue < rightValue) return -1;
  }

  return 0;
}

function latestVersionFromNpm() {
  const result = spawnSync("npm", ["view", packageName, "version"], {
    encoding: "utf8",
    shell: process.platform === "win32",
    timeout: 2500
  });

  if (result.error || result.status !== 0) {
    return null;
  }

  const latest = String(result.stdout || "").trim();
  return latest || null;
}

function shouldSkipUpdateCheck(args) {
  if (process.env.MIND_NO_UPDATE_CHECK === "1") return true;
  if (!process.stdin.isTTY || !process.stdout.isTTY) return true;
  if (args.includes("--upgrade")) return true;
  if (!packageName || !currentVersion) return true;
  return false;
}

async function installLatestPackage() {
  console.log(`\nInstalling ${packageName}@latest...\n`);

  const child = spawn("npm", ["install", "-g", packageName], {
    stdio: "inherit",
    shell: process.platform === "win32"
  });

  const code = await new Promise((resolve) => {
    child.on("exit", (exitCode) => resolve(exitCode ?? 1));
    child.on("error", () => resolve(1));
  });

  process.exit(code);
}

async function promptForUpdate(latestVersion, state, now) {
  console.log(`\n\u2728 Update available! ${currentVersion} -> ${latestVersion}\n`);
  console.log(`Release notes: https://www.npmjs.com/package/${packageName}\n`);
  console.log(`1. Update now (runs \`npm install -g ${packageName}\`)`);
  console.log("2. Skip");
  console.log("3. Skip until next version\n");

  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout
  });

  const answer = (await rl.question("Press enter to continue [2]: ")).trim();
  rl.close();

  const choice = answer || "2";

  if (choice === "1") {
    await installLatestPackage();
    return;
  }

  if (choice === "3") {
    saveUpdateState({
      ...state,
      last_checked_at: now,
      skip_until_version: latestVersion
    });
  }
}

async function maybeCheckPackageUpdate(args) {
  if (shouldSkipUpdateCheck(args)) return;

  const state = loadUpdateState();
  const now = Date.now();
  const lastCheckedAt = Number(state.last_checked_at || 0);

  if (now - lastCheckedAt < updateCheckIntervalMs) {
    return;
  }

  const latestVersion = latestVersionFromNpm();
  saveUpdateState({ ...state, last_checked_at: now });

  if (!latestVersion) return;
  if (compareVersions(latestVersion, currentVersion) <= 0) return;
  if (state.skip_until_version === latestVersion) return;

  await promptForUpdate(latestVersion, state, now);
}

function platformPackageName() {
  if (process.platform === "win32") return "@proxymind/mind-win32";
  if (process.platform === "darwin") return "@proxymind/mind-darwin";
  return null;
}

function resolvePackageRoot(name) {
  try {
    return path.dirname(require.resolve(`${name}/package.json`));
  } catch {
    return null;
  }
}

function resolveMindBinary() {
  const packageForPlatform = platformPackageName();

  if (!packageForPlatform) {
    return {
      error: `Mind is not supported on this platform: ${process.platform}`
    };
  }

  const runtimeRoot = resolvePackageRoot(packageForPlatform);

  if (!runtimeRoot) {
    return {
      error: `Platform package not found: ${packageForPlatform}. Please reinstall ${packageName}.`
    };
  }

  const candidates = process.platform === "win32"
    ? [
        {
          binary: path.join(runtimeRoot, "applications", "MindEngine", "mind.exe"),
          cwd: path.join(runtimeRoot, "applications", "MindEngine")
        },
        {
          binary: path.join(runtimeRoot, "applications", "MindEngine", "Mind.exe"),
          cwd: path.join(runtimeRoot, "applications", "MindEngine")
        }
      ]
    : [
        {
          binary: path.join(runtimeRoot, "applications", "Mind.app", "Contents", "MacOS", "mind"),
          cwd: path.join(runtimeRoot, "applications", "Mind.app", "Contents", "MacOS")
        },
        {
          binary: path.join(runtimeRoot, "applications", "Mind.app", "Contents", "MacOS", "Mind"),
          cwd: path.join(runtimeRoot, "applications", "Mind.app", "Contents", "MacOS")
        }
      ];

  for (const candidate of candidates) {
    if (existsSync(candidate.binary)) {
      return candidate;
    }
  }

  return {
    error: `Mind binary not found in ${packageForPlatform}. Please reinstall ${packageName}.`
  };
}

await maybeCheckPackageUpdate(process.argv.slice(2));

const resolved = resolveMindBinary();

if (resolved.error) {
  console.error(resolved.error);
  process.exit(1);
}

const child = spawn(resolved.binary, process.argv.slice(2), {
  cwd: resolved.cwd,
  stdio: "inherit",
  windowsHide: false
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.exit(signal === "SIGINT" ? 130 : 1);
    return;
  }
  process.exit(code ?? 1);
});

child.on("error", (error) => {
  console.error(error.message);
  process.exit(1);
});
