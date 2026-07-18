"use strict";

const assert = require("assert");

const {
  assertUiApiCompatible,
  compareSemanticVersions,
  parseSemanticVersion
} = require("../src/compatibility");

assert.deepStrictEqual(parseSemanticVersion("aq 0.2.0"), [0, 2, 0]);
assert.deepStrictEqual(parseSemanticVersion("aq 1.4.12\n"), [1, 4, 12]);
assert.strictEqual(parseSemanticVersion("unknown"), undefined);
assert(compareSemanticVersions([0, 3, 0], [0, 2, 9]) > 0);
assert.strictEqual(assertUiApiCompatible("aq 0.2.0"), "0.2.0");
assert.strictEqual(assertUiApiCompatible("aq 1.0.0"), "1.0.0");
assert.throws(
  () => assertUiApiCompatible("aq 0.1.9"),
  /0\.2\.0 or newer is required/
);
assert.throws(() => assertUiApiCompatible("not a version"), /unable to parse/);
