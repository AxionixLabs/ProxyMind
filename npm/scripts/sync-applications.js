#!/usr/bin/env node

import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const workspaceRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const repoRoot = path.dirname(workspaceRoot);
const sourceApplications = path.join(repoRoot, "applications");

const syncTargets = [
  {
    name: "win32",
    required: [
      {
        source: path.join(sourceApplications, "MindEngine"),
        target: path.join(workspaceRoot, "packages", "mind-win32", "applications", "MindEngine")
      },
      {
        source: path.join(sourceApplications, "mind.bat"),
        target: path.join(workspaceRoot, "packages", "mind-win32", "applications", "mind.bat")
      },
      {
        source: path.join(sourceApplications, "Structure"),
        target: path.join(workspaceRoot, "packages", "mind-win32", "applications", "Structure")
      }
    ],
    optional: []
  },
  {
    name: "darwin",
    required: [
      {
        source: path.join(sourceApplications, "Mind.app"),
        target: path.join(workspaceRoot, "packages", "mind-darwin", "applications", "Mind.app")
      }
    ],
    optional: []
  }
];

let synced = 0;

for (const item of syncTargets) {
  const missingRequired = item.required.filter((entry) => !existsSync(entry.source));

  if (missingRequired.length > 0) {
    for (const entry of missingRequired) {
      console.warn(`Skipped ${item.name}: source not found: ${entry.source}`);
    }
    continue;
  }

  const platformApplications = path.join(workspaceRoot, "packages", `mind-${item.name}`, "applications");
  rmSync(platformApplications, { recursive: true, force: true });

  for (const entry of [...item.required, ...item.optional]) {
    if (!existsSync(entry.source)) {
      console.warn(`Skipped optional ${item.name}: source not found: ${entry.source}`);
      continue;
    }

    mkdirSync(path.dirname(entry.target), { recursive: true });
    cpSync(entry.source, entry.target, { recursive: true });
    console.log(`Synced ${item.name}: ${entry.source} -> ${entry.target}`);
  }

  synced += 1;
}

if (synced === 0) {
  console.error("No platform applications were synced.");
  process.exit(1);
}
