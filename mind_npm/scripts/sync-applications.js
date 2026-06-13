#!/usr/bin/env node

import { cpSync, existsSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const repoRoot = path.dirname(packageRoot);
const source = path.join(repoRoot, "applications");
const target = path.join(packageRoot, "applications");

if (!existsSync(source)) {
  console.error(`applications directory not found: ${source}`);
  process.exit(1);
}

rmSync(target, { recursive: true, force: true });
cpSync(source, target, { recursive: true });

console.log(`Synced ${source} -> ${target}`);
