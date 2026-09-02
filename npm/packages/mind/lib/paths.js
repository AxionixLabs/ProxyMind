import os from "node:os";
import path from "node:path";

function mindHome() {
  return path.resolve(process.env.MIND_HOME || path.join(os.homedir(), ".mind"));
}

function versionPath() {
  return path.join(mindHome(), "version.json");
}

export {
  mindHome,
  versionPath
};
