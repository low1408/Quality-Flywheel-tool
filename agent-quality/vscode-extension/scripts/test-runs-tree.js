"use strict";

const assert = require("assert");
const fs = require("fs");
const Module = require("module");
const os = require("os");
const path = require("path");

class TreeItem {
  constructor(label, collapsibleState) {
    this.label = label;
    this.collapsibleState = collapsibleState;
  }
}

class EventEmitter {
  constructor() {
    this.event = () => undefined;
  }

  fire() {}

  dispose() {}
}

class ThemeIcon {
  constructor(id) {
    this.id = id;
  }
}

const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "aq-runs-tree-"));
const folderA = workspaceFolder("alpha", path.join(tempRoot, "alpha"), "repo-a");
const folderADuplicate = workspaceFolder(
  "alpha-subfolder",
  path.join(tempRoot, "alpha", "packages", "one"),
  "repo-a"
);
const folderB = workspaceFolder("beta", path.join(tempRoot, "beta"), "repo-b");
for (const folder of [folderA, folderB]) {
  fs.mkdirSync(path.join(folder.uri.fsPath, ".agent-quality", "local"), {
    recursive: true
  });
  fs.writeFileSync(
    path.join(folder.uri.fsPath, ".agent-quality", "local", "quality.sqlite3"),
    ""
  );
}

const vscode = {
  EventEmitter,
  ThemeIcon,
  TreeItem,
  TreeItemCollapsibleState: { None: 0, Collapsed: 1 },
  window: { showInputBox: async () => undefined },
  workspace: { workspaceFolders: [folderA, folderADuplicate, folderB] }
};
const calls = [];
const runtime = {
  agentQualityHome: (folder) => path.join(folder.uri.fsPath, ".agent-quality", "local"),
  execAq: async (_args, folder) => {
    calls.push(folder.name);
    return {
      stdout: [
        "runs: 1",
        `2026-07-17T00:00:00Z run_${folder.name} agent=completed verifier=passed human=accepted_cleanly`
      ].join("\n")
    };
  },
  projectRootPath: (folder) => folder.repositoryRoot
};

const originalLoad = Module._load;
Module._load = function load(request, parent, isMain) {
  if (request === "vscode") return vscode;
  if (request === "./runtime" && parent.filename.endsWith("runs-tree.js")) {
    return runtime;
  }
  return originalLoad.call(this, request, parent, isMain);
};

async function main() {
  const { RunsProvider } = require("../src/runs-tree");
  const provider = new RunsProvider();
  await provider.load();

  const roots = provider.getChildren();
  assert.deepStrictEqual(roots.map((item) => item.label), ["alpha", "beta"]);
  assert(roots.every((item) => item.collapsibleState === 1));
  const alphaRuns = provider.getChildren(roots[0]);
  const alphaRun = alphaRuns.find((item) => item.runId);
  assert.strictEqual(alphaRun.runId, "run_alpha");
  assert.strictEqual(alphaRun.workspaceFolder, folderA);
  assert.deepStrictEqual(calls, ["alpha", "beta"]);
  provider.dispose();
}

function workspaceFolder(name, fsPath, repositoryRoot) {
  return { name, repositoryRoot, uri: { fsPath } };
}

main().finally(() => {
  Module._load = originalLoad;
  fs.rmSync(tempRoot, { recursive: true, force: true });
}).catch((err) => {
  process.stderr.write(`${err.stack || err}\n`);
  process.exitCode = 1;
});
