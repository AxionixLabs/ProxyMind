#!/usr/bin/env node

import { existsSync } from "node:fs";
import path from "node:path";

const candidates = process.argv.slice(2);

if (candidates.length === 0) {
  console.error("No runtime candidates were provided.");
  process.exit(1);
}

for (const candidate of candidates) {
  const target = path.resolve(process.cwd(), candidate);
  if (existsSync(target)) {
    process.exit(0);
  }
}

console.error("Required runtime binary not found. Checked:");
for (const candidate of candidates) {
  console.error(`- ${path.resolve(process.cwd(), candidate)}`);
}
process.exit(1);
