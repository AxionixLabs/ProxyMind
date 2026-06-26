import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { updateStateDir, updateStatePath } from "./paths.js";

const updateCheckTimeoutMs = 15000;

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

function saveUpdateError(state, startedAt, error) {
  saveUpdateState({
    ...state,
    last_attempted_at: startedAt || Date.now(),
    update_error: error
  });
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
  timeout: updateCheckTimeoutMs,
  windowsHide: true
});

const state = loadUpdateState();

if (result.error || result.status !== 0) {
  saveUpdateError(state, startedAt, {
    type: result.error?.code || "npm_view_failed",
    status: result.status,
    signal: result.signal,
    message: String(result.error?.message || result.stderr || "").trim()
  });
  process.exit(0);
}

const latestVersion = String(result.stdout || "").trim();

if (!latestVersion) {
  saveUpdateError(state, startedAt, {
    type: "empty_version",
    status: result.status,
    signal: result.signal,
    message: "npm view returned an empty version"
  });
  process.exit(0);
}

saveUpdateState({
  ...state,
  last_attempted_at: startedAt || Date.now(),
  last_checked_at: startedAt || Date.now(),
  latest_version: latestVersion,
  update_error: null
});
