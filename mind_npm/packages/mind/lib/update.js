import { spawn } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

import { isVersionGreater } from "./version.js";

const updateCheckIntervalMs = 24 * 60 * 60 * 1000;
const updateRetryIntervalMs = 60 * 60 * 1000;
const color = {
  reset: "\x1b[0m",
  bold: "\x1b[1m",
  dim: "\x1b[2m",
  blue: "\x1b[38;5;75m",
  cyan: "\x1b[38;5;37m",
  selectedFg: "\x1b[38;5;195m",
  yellow: "\x1b[33m"
};

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

function npmCommand(args) {
  if (process.platform !== "win32") {
    return { command: "npm", args };
  }

  return {
    command: process.env.ComSpec || "cmd.exe",
    args: ["/d", "/s", "/c", "npm", ...args]
  };
}

function updateWorkerPath() {
  return fileURLToPath(new URL("./update-worker.js", import.meta.url));
}

function shouldSkipUpdateCheck(args, packageInfo) {
  if (process.env.MIND_NO_UPDATE_CHECK === "1") return true;
  if (!process.stdin.isTTY || !process.stdout.isTTY) return true;
  if (args.includes("--upgrade")) return true;
  if (!packageInfo.packageName || !packageInfo.currentVersion) return true;
  return false;
}

function refreshLatestVersionInBackground(packageName, state, now) {
  const child = spawn(process.execPath, [updateWorkerPath(), packageName, String(now)], {
    stdio: "ignore",
    detached: true,
    windowsHide: true
  });

  child.unref();
}

function maybeRefreshLatestVersion(packageName, state, now) {
  const lastAttemptedAt = Number(state.last_attempted_at || state.last_checked_at || 0);
  const interval = state.update_error ? updateRetryIntervalMs : updateCheckIntervalMs;
  if (now - lastAttemptedAt < interval) return;

  saveUpdateState({ ...state, last_attempted_at: now });
  refreshLatestVersionInBackground(packageName, state, now);
}

async function installLatestPackage(packageName) {
  console.log(`\nInstalling ${packageName}@latest...\n`);

  const npm = npmCommand(["install", "-g", packageName]);
  const child = spawn(npm.command, npm.args, {
    stdio: "inherit"
  });

  const code = await new Promise((resolve) => {
    child.on("exit", (exitCode) => resolve(exitCode ?? 1));
    child.on("error", () => resolve(1));
  });

  process.exit(code);
}

async function promptForUpdate(latestVersion, state, now, packageInfo) {
  writeUpdatePromptHeader(latestVersion, packageInfo);

  const choice = await selectUpdateChoice(updateMenuItems(packageInfo), 0);

  if (choice === "1") {
    await installLatestPackage(packageInfo.packageName);
    return;
  }

  if (choice === "3") {
    saveUpdateState({
      ...state,
      last_checked_at: now,
      skip_until_version: latestVersion
    });
    return;
  }

  if (choice === "exit") {
    process.exit(130);
  }
}

function writeUpdatePromptHeader(latestVersion, packageInfo) {
  console.log(
    `\n${color.bold}${color.yellow}\u2728 Update available!${color.reset} ` +
    `${color.dim}${packageInfo.currentVersion}${color.reset} -> ${color.blue}${latestVersion}${color.reset}\n`
  );
  console.log(
    `${color.dim}Release notes:${color.reset} ` +
    `https://www.npmjs.com/package/${packageInfo.packageName}\n`
  );
}

function updateMenuItems(packageInfo) {
  return [
    `Update now (runs \`npm install -g ${packageInfo.packageName}\`)`,
    "Skip",
    "Skip until next version"
  ];
}

function renderUpdateMenu(items, selected) {
  process.stdout.write("\x1b[?25l");
  for (let index = 0; index < items.length; index += 1) {
    process.stdout.write(`${formatMenuLine(items, index, selected)}\n`);
  }
  process.stdout.write(`\n${color.dim}Press enter to continue, Esc to exit${color.reset}`);
}

function formatMenuLine(items, index, selected) {
  const active = index === selected;
  const line = `${menuMarker(active)} ${index + 1}. ${items[index]}`;

  if (active) {
    return `${color.selectedFg}${color.bold}${line}${color.reset}`;
  }

  return `${line[0]} ${color.cyan}${line.slice(2)}${color.reset}`;
}

function menuMarker(active) {
  return active
    ? "\u203a"
    : " ";
}

function writeMenuLine(items, index, selected) {
  readline.cursorTo(process.stdout, 0);
  readline.clearLine(process.stdout, 0);
  process.stdout.write(formatMenuLine(items, index, selected));
}

function moveMenuSelection(items, previous, selected) {
  const lineCount = items.length + 2;
  const promptLine = lineCount - 1;

  readline.moveCursor(process.stdout, 0, -(promptLine - previous));
  writeMenuLine(items, previous, selected);

  readline.moveCursor(process.stdout, 0, selected - previous);
  writeMenuLine(items, selected, selected);

  readline.moveCursor(process.stdout, 0, promptLine - selected);
  readline.cursorTo(process.stdout, 0);
}

async function selectUpdateChoice(items, defaultIndex) {
  let selected = Math.max(0, Math.min(items.length - 1, defaultIndex));
  const wasRaw = process.stdin.isRaw;

  readline.emitKeypressEvents(process.stdin);
  process.stdin.setRawMode(true);
  process.stdin.resume();

  renderUpdateMenu(items, selected);

  return await new Promise((resolve) => {
    const finish = (value) => {
      process.stdin.off("keypress", onKeypress);
      process.stdin.setRawMode(wasRaw);
      process.stdin.pause();
      process.stdout.write("\x1b[?25h\n\n");
      resolve(value);
    };

    const onKeypress = (_str, key) => {
      if (key.ctrl && key.name === "c") {
        finish("exit");
        return;
      }

      if (key.name === "escape") {
        finish("exit");
        return;
      }

      if (key.name === "up") {
        const previous = selected;
        selected = selected === 0 ? items.length - 1 : selected - 1;
        moveMenuSelection(items, previous, selected);
        return;
      }

      if (key.name === "down") {
        const previous = selected;
        selected = selected === items.length - 1 ? 0 : selected + 1;
        moveMenuSelection(items, previous, selected);
        return;
      }

      if (key.name === "return" || key.name === "enter") {
        finish(String(selected + 1));
        return;
      }

      if (["1", "2", "3"].includes(key.sequence)) {
        finish(key.sequence);
      }
    };

    process.stdin.on("keypress", onKeypress);
  });
}

async function maybeCheckPackageUpdate(args, packageInfo) {
  if (shouldSkipUpdateCheck(args, packageInfo)) return;

  const state = loadUpdateState();
  const now = Date.now();
  const latestVersion = String(state.latest_version || "").trim();

  maybeRefreshLatestVersion(packageInfo.packageName, state, now);

  if (!latestVersion) return;
  if (!isVersionGreater(latestVersion, packageInfo.currentVersion)) return;
  if (state.skip_until_version === latestVersion) return;

  await promptForUpdate(latestVersion, state, now, packageInfo);
}

export {
  loadUpdateState,
  maybeCheckPackageUpdate,
  saveUpdateState,
  shouldSkipUpdateCheck,
  updateStatePath
};
