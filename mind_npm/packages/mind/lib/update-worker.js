import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

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
    // best effort
  }
}

function npmCommand(args) {
  if (process.platform !== "win32") {
    return { command: "npm", args };
  }

  return {
    command: process.env.ComSpec || "cmd.exe",
    args: ["/d", "/s", "/c", "npm", ...args]
  };
}

const packageName = process.argv[2] || "";
const startedAt = Number(process.argv[3] || 0);

if (!packageName) {
  process.exit(0);
}

const npm = npmCommand(["view", packageName, "version"]);
const result = spawnSync(npm.command, npm.args, {
  encoding: "utf8",
  timeout: 2500,
  windowsHide: true
});

if (result.error || result.status !== 0) {
  process.exit(0);
}

const latestVersion = String(result.stdout || "").trim();

if (!latestVersion) {
  process.exit(0);
}

const state = loadUpdateState();
saveUpdateState({
  ...state,
  last_checked_at: startedAt || Date.now(),
  latest_version: latestVersion
});
