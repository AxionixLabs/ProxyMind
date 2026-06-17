import test from "node:test";
import assert from "node:assert/strict";

import { compareVersions, isVersionGreater, parseVersion } from "../lib/version.js";

test("parseVersion parses semver parts", () => {
  assert.deepEqual(parseVersion("v1.2.3-beta.4+build.7"), {
    parts: [1, 2, 3],
    prerelease: ["beta", 4]
  });
});

test("compareVersions orders release versions", () => {
  assert.equal(compareVersions("0.140.0", "0.139.0"), 1);
  assert.equal(compareVersions("0.139.0", "0.140.0"), -1);
  assert.equal(compareVersions("1.2.3", "1.2.3"), 0);
});

test("compareVersions handles prerelease versions", () => {
  assert.equal(compareVersions("1.0.0-beta.1", "1.0.0"), -1);
  assert.equal(compareVersions("1.0.0", "1.0.0-beta.1"), 1);
  assert.equal(compareVersions("1.0.0-alpha.1", "1.0.0-alpha.beta"), -1);
});

test("compareVersions ignores build metadata", () => {
  assert.equal(compareVersions("1.2.3+build.1", "1.2.3+build.2"), 0);
});

test("isVersionGreater is consistent", () => {
  assert.equal(isVersionGreater("2.0.0", "1.9.9"), true);
  assert.equal(isVersionGreater("1.0.0-alpha", "1.0.0"), false);
});
