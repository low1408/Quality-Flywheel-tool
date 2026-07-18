"use strict";

const MIN_UI_API_AQ_VERSION = "0.2.0";

function parseSemanticVersion(value) {
  const match = /(?:^|\s)(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?(?:\s|$)/.exec(
    String(value).trim()
  );
  if (!match) return undefined;
  return match.slice(1, 4).map(Number);
}

function compareSemanticVersions(left, right) {
  for (let index = 0; index < 3; index += 1) {
    if (left[index] !== right[index]) return left[index] - right[index];
  }
  return 0;
}

function assertUiApiCompatible(versionOutput) {
  const installed = parseSemanticVersion(versionOutput);
  const required = parseSemanticVersion(MIN_UI_API_AQ_VERSION);
  if (!installed) {
    throw new Error(`unable to parse aq version from: ${String(versionOutput).trim()}`);
  }
  if (compareSemanticVersions(installed, required) < 0) {
    throw new Error(
      `aq ${installed.join(".")} is installed; ${MIN_UI_API_AQ_VERSION} or newer is required`
    );
  }
  return installed.join(".");
}

module.exports = {
  MIN_UI_API_AQ_VERSION,
  assertUiApiCompatible,
  compareSemanticVersions,
  parseSemanticVersion
};
