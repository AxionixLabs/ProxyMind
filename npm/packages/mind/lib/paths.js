import os from "node:os";
import path from "node:path";

function mindStateHome() {
  return path.resolve(
    process.env.MIND_STATE_HOME ||
    process.env.MIND_HOME ||
    path.join(os.homedir(), ".mind")
  );
}

function versionPath() {
  return path.join(mindStateHome(), "version.json");
}

export {
  mindStateHome,
  versionPath
};
