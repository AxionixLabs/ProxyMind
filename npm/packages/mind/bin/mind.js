#!/usr/bin/env node

import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { maybeCheckPackageUpdate } from "../lib/update.js";

const require = createRequire(import.meta.url);
const packageRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const packageJson = JSON.parse(readFileSync(path.join(packageRoot, "package.json"), "utf8"));
const packageName = packageJson.name;
const currentVersion = packageJson.version;

function platformPackageName() {
  if (process.platform === "win32") return "@craftline/mind-win32";
  if (process.platform === "darwin") return "@craftline/mind-darwin";
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

  const binary = process.platform === "win32"
    ? path.join(runtimeRoot, "applications", "MindEngine", "mind.exe")
    : path.join(runtimeRoot, "applications", "Mind.app", "Contents", "MacOS", "mind");

  if (existsSync(binary)) {
    return {
      binary,
      cwd: process.cwd()
    };
  }

  return {
    error: `Mind binary not found in ${packageForPlatform}. Please reinstall ${packageName}.`
  };
}

await maybeCheckPackageUpdate(process.argv.slice(2), {
  packageName,
  currentVersion
});

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
