import test from "node:test";
import assert from "node:assert/strict";

import { shouldSkipUpdateCheck } from "../lib/update.js";

test("shouldSkipUpdateCheck respects env flag", () => {
  const oldValue = process.env.MIND_NO_UPDATE_CHECK;
  process.env.MIND_NO_UPDATE_CHECK = "1";

  assert.equal(
    shouldSkipUpdateCheck([], { packageName: "@craftline/mind", currentVersion: "1.0.0" }),
    true
  );

  if (oldValue === undefined) {
    delete process.env.MIND_NO_UPDATE_CHECK;
  } else {
    process.env.MIND_NO_UPDATE_CHECK = oldValue;
  }
});
