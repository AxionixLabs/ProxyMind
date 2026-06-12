#!/usr/bin/env node

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const appDir = path.join(packageRoot, "applications");

const engineName = "MindEngine";
const executableNames = process.platform === "win32"
  ? ["mind.exe", "Mind.exe"]
  : ["mind", "Mind"];

function resolveMindBinary() {
  const engineDir = path.join(appDir, engineName);
  for (const executableName of executableNames) {
    const binary = path.join(engineDir, executableName);
    if (existsSync(binary)) {
      return { binary, cwd: engineDir };
    }
  }

  return null;
}

const resolved = resolveMindBinary();

if (!resolved) {
  console.error(`Mind binary not found under ${appDir}`);
  process.exit(1);
}

const child = spawn(resolved.binary, process.argv.slice(2), {
  cwd: resolved.cwd,
  stdio: "inherit",
  windowsHide: false
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 1);
});

child.on("error", (error) => {
  console.error(error.message);
  process.exit(1);
});
