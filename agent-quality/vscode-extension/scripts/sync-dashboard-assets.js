"use strict";

const fs = require("fs");
const path = require("path");

const extensionRoot = path.resolve(__dirname, "..");
const canonicalRoot = path.resolve(
  extensionRoot,
  "..",
  "src",
  "agent_quality",
  "collector",
  "static"
);
const generatedRoot = path.join(extensionRoot, "media");
const assets = ["dashboard.html", "dashboard.css", "dashboard.js"];
const checkOnly = process.argv.includes("--check");

for (const asset of assets) {
  const source = path.join(canonicalRoot, asset);
  const generated = path.join(generatedRoot, asset);
  if (checkOnly) {
    if (!fs.existsSync(generated)
        || !fs.readFileSync(source).equals(fs.readFileSync(generated))) {
      process.stderr.write(
        `${asset} is stale; run npm run sync-dashboard-assets\n`
      );
      process.exitCode = 1;
    }
    continue;
  }
  fs.copyFileSync(source, generated);
}
