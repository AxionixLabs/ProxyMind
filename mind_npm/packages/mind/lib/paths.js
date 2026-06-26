import os from "node:os";
import path from "node:path";

function mindHome() {
  return path.resolve(process.env.MIND_HOME || path.join(os.homedir(), ".mind"));
}

function updateStateDir() {
  return path.join(mindHome(), "state");
}

function updateStatePath() {
  return path.join(updateStateDir(), "update-check.json");
}

export {
  mindHome,
  updateStateDir,
  updateStatePath
};
