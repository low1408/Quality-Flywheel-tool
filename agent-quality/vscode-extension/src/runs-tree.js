"use strict";

const fs = require("fs");
const path = require("path");
const vscode = require("vscode");

const {
  agentQualityHome,
  execAq,
  projectRootPath
} = require("./runtime");

class RunsProvider {
  constructor() {
    this.emitter = new vscode.EventEmitter();
    this.onDidChangeTreeData = this.emitter.event;
    this.items = [new MessageItem("No runs loaded")];
  }

  refresh() {
    void this.load();
  }

  getTreeItem(item) {
    return item;
  }

  getChildren(item) {
    return item && Array.isArray(item.children) ? item.children : this.items;
  }

  dispose() {
    this.emitter.dispose();
  }

  async load() {
    const folders = vscode.workspace.workspaceFolders || [];
    if (!folders.length) {
      this.setItems([new MessageItem("Open a workspace to use Agent Quality")]);
      return;
    }
    const targets = uniqueRepositoryFolders(folders);
    const workspaces = await Promise.all(
      targets.map(async (folder) => ({
        folder,
        items: await this.loadWorkspace(folder)
      }))
    );
    if (workspaces.length === 1) {
      this.setItems(workspaces[0].items);
      return;
    }
    this.setItems(workspaces.map(({ folder, items }) => (
      new WorkspaceItem(folder, items)
    )));
  }

  async loadWorkspace(folder) {
    if (!fs.existsSync(path.join(agentQualityHome(folder), "quality.sqlite3"))) {
      return [new MessageItem("No Agent Quality runs yet")];
    }
    try {
      const result = await execAq(["report", "summary"], folder);
      return parseSummary(result.stdout, folder);
    } catch (err) {
      return [new MessageItem(err.message || String(err))];
    }
  }

  setItems(items) {
    this.items = items;
    this.emitter.fire();
  }
}

class WorkspaceItem extends vscode.TreeItem {
  constructor(folder, children) {
    super(folder.name, vscode.TreeItemCollapsibleState.Collapsed);
    this.children = children;
    this.contextValue = "workspace";
    this.description = projectRootPath(folder);
    this.resourceUri = folder.uri;
  }
}

class MessageItem extends vscode.TreeItem {
  constructor(label) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.contextValue = "message";
  }
}

class RunItem extends vscode.TreeItem {
  constructor(run, workspaceFolder) {
    super(`${run.id} ${run.verifier || "unverified"}`, vscode.TreeItemCollapsibleState.None);
    this.runId = run.id;
    this.workspaceFolder = workspaceFolder;
    this.contextValue = "run";
    this.description = run.agent;
    this.tooltip = `${run.startedAt}\nagent=${run.agent}\nverifier=${run.verifier}\nhuman=${run.human}`;
    this.command = {
      command: "agentQuality.showRun",
      title: "Show Run",
      arguments: [this]
    };
    this.iconPath = new vscode.ThemeIcon(iconForRun(run));
  }
}

function parseSummary(stdout, workspaceFolder) {
  const lines = stdout.split(/\r?\n/);
  const items = [];
  const counts = lines.filter((line) => (
    /^(runs|completed|verified_passed|reviewed|accepted):/.test(line)
  ));
  if (counts.length) {
    items.push(new MessageItem(counts.join("  ")));
  }
  const runPattern = /^\s*(\S+)\s+(run_[^\s]+)\s+agent=([^\s]+)\s+verifier=([^\s]+)\s+human=([^\s]+)/;
  for (const line of lines) {
    const match = runPattern.exec(line);
    if (match) {
      items.push(new RunItem({
        startedAt: match[1],
        id: match[2],
        agent: match[3],
        verifier: match[4],
        human: match[5]
      }, workspaceFolder));
    }
  }
  return items.length ? items : [new MessageItem("No Agent Quality runs yet")];
}

function iconForRun(run) {
  if (run.verifier === "passed") return "pass";
  if (run.verifier === "failed" || run.agent === "failed") return "error";
  return "circle-outline";
}

function uniqueRepositoryFolders(folders) {
  const seen = new Set();
  return folders.filter((folder) => {
    const root = projectRootPath(folder);
    if (seen.has(root)) return false;
    seen.add(root);
    return true;
  });
}

async function resolveRunId(item) {
  if (item && item.runId) {
    return item.runId;
  }
  return vscode.window.showInputBox({
    title: "Agent Quality Run ID",
    prompt: "Run ID",
    ignoreFocusOut: true
  });
}

module.exports = { RunsProvider, resolveRunId };
