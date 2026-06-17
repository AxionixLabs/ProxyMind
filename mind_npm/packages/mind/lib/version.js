function parseVersion(version) {
  const match = String(version || "")
    .trim()
    .match(/^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/);

  if (!match) return null;

  return {
    parts: [
      Number.parseInt(match[1], 10),
      Number.parseInt(match[2] || "0", 10),
      Number.parseInt(match[3] || "0", 10)
    ],
    prerelease: match[4]
      ? match[4].split(".").map((part) => {
          if (/^\d+$/.test(part)) return Number.parseInt(part, 10);
          return part;
        })
      : []
  };
}

function comparePrerelease(left, right) {
  const length = Math.max(left.length, right.length);

  for (let index = 0; index < length; index += 1) {
    const leftValue = left[index];
    const rightValue = right[index];

    if (leftValue === undefined) return -1;
    if (rightValue === undefined) return 1;
    if (leftValue === rightValue) continue;

    const leftNumeric = typeof leftValue === "number";
    const rightNumeric = typeof rightValue === "number";

    if (leftNumeric && rightNumeric) return leftValue > rightValue ? 1 : -1;
    if (leftNumeric) return -1;
    if (rightNumeric) return 1;
    if (String(leftValue) > String(rightValue)) return 1;
    if (String(leftValue) < String(rightValue)) return -1;
  }

  return 0;
}

function compareVersions(left, right) {
  const leftVersion = parseVersion(left);
  const rightVersion = parseVersion(right);

  if (!leftVersion || !rightVersion) {
    if (String(left || "") > String(right || "")) return 1;
    if (String(left || "") < String(right || "")) return -1;
    return 0;
  }

  for (let index = 0; index < 3; index += 1) {
    const leftValue = leftVersion.parts[index];
    const rightValue = rightVersion.parts[index];
    if (leftValue > rightValue) return 1;
    if (leftValue < rightValue) return -1;
  }

  if (!leftVersion.prerelease.length && !rightVersion.prerelease.length) return 0;
  if (!leftVersion.prerelease.length) return 1;
  if (!rightVersion.prerelease.length) return -1;
  return comparePrerelease(leftVersion.prerelease, rightVersion.prerelease);
}

function isVersionGreater(left, right) {
  return compareVersions(left, right) > 0;
}

export {
  compareVersions,
  isVersionGreater,
  parseVersion
};
