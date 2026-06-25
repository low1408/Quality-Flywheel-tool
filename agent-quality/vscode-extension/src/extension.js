"use strict";

const cp = require("child_process");
const fs = require("fs");
const path = require("path");
const vscode = require("vscode");

let output;
let statusItem;
let collectorProcess;
let runsProvider;

function activate(context) {
  output = vscode.window.createOutputChannel("Agent Quality");
  statusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  statusItem.text = "$(pulse) Agent Quality";
  statusItem.command = "agentQuality.reportSummary";
  statusItem.tooltip = "Show Agent Quality summary";
  statusItem.show();

  runsProvider = new RunsProvider();
  context.subscriptions.push(output, statusItem, runsProvider);
  context.subscriptions.push(vscode.window.registerTreeDataProvider("agentQuality.runs", runsProvider));

  register(context, "agentQuality.initProject", initProject);
  register(context, "agentQuality.runPrompt", runPrompt);
  register(context, "agentQuality.runSelection", runSelection);
  register(context, "agentQuality.installCodexHooks", installCodexHooks);
  register(context, "agentQuality.startCollector", startCollector);
  register(context, "agentQuality.stopCollector", stopCollector);
  register(context, "agentQuality.reportSummary", reportSummary);
  register(context, "agentQuality.refreshRuns", () => runsProvider.refresh());
  register(context, "agentQuality.showDashboard", () => DashboardPanel.show(context));
  register(context, "agentQuality.showRun", (item) => showDashboardRun(context, item, "overview"));
  register(context, "agentQuality.diffRun", (item) => showDashboardRun(context, item, "artifacts"));
  register(context, "agentQuality.traceRun", (item) => showDashboardRun(context, item, "timeline"));
  register(context, "agentQuality.reviewRun", (item) => showDashboardRun(context, item, "review"));

  runsProvider.refresh();
}

function deactivate() {
  if (collectorProcess) {
    collectorProcess.kill();
    collectorProcess = undefined;
  }
}

function register(context, command, handler) {
  context.subscriptions.push(vscode.commands.registerCommand(command, handler));
}

async function initProject() {
  const folder = await pickWorkspaceFolder();
  if (!folder) {
    return;
  }
  await runAq(["init", "--repo", folder.uri.fsPath], folder, { title: "Initialize project" });
  runsProvider.refresh();
}

async function runPrompt() {
  const folder = await pickWorkspaceFolder();
  if (!folder) {
    return;
  }
  const prompt = await vscode.window.showInputBox({
    title: "Agent Quality: Run Prompt",
    prompt: "Prompt to pass to aq run",
    ignoreFocusOut: true
  });
  if (!prompt) {
    return;
  }
  await runMeasuredPrompt(folder, prompt);
}

async function runSelection() {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showWarningMessage("Open a file and select prompt text first.");
    return;
  }
  const selection = editor.document.getText(editor.selection).trim();
  if (!selection) {
    vscode.window.showWarningMessage("Select prompt text first.");
    return;
  }
  const folder = vscode.workspace.getWorkspaceFolder(editor.document.uri) || await pickWorkspaceFolder();
  if (!folder) {
    return;
  }
  await runMeasuredPrompt(folder, selection);
}

async function runMeasuredPrompt(folder, prompt) {
  const cfg = getConfig();
  const args = ["run", "--repo", folder.uri.fsPath];
  const verifyPath = configuredVerifyPath(folder);
  if (verifyPath) {
    args.push("--verify", verifyPath);
  }
  if (cfg.get("allowDirtyRuns")) {
    args.push("--allow-dirty");
  }
  const model = cfg.get("model");
  if (model) {
    args.push("--model", model);
  }
  args.push(prompt);

  await runAq(args, folder, { title: "Run prompt", reveal: true });
  runsProvider.refresh();
}

async function installCodexHooks() {
  const folder = await pickWorkspaceFolder();
  if (!folder) {
    return;
  }
  const pythonPath = getConfig().get("pythonPath") || "python3";
  await runAq(["install-codex-hooks", "--repo", folder.uri.fsPath, "--python", pythonPath], folder, {
    title: "Install Codex hooks"
  });
}

async function startCollector() {
  if (collectorProcess) {
    vscode.window.showInformationMessage("Agent Quality collector is already running.");
    return;
  }

  const folder = await pickWorkspaceFolder();
  if (!folder) {
    return;
  }
  const cfg = getConfig();
  const args = [
    "serve-collector",
    "--host",
    cfg.get("collectorHost") || "127.0.0.1",
    "--port",
    String(cfg.get("collectorPort") || 8765)
  ];
  const token = cfg.get("collectorToken");
  if (token) {
    args.push("--token", token);
  }

  const invocation = aqInvocation(folder);
  output.show(true);
  output.appendLine(`$ ${quoteArgs([...invocation.commandLine, ...args])}`);
  collectorProcess = cp.spawn(invocation.command, [...invocation.prefixArgs, ...args], {
    cwd: folder.uri.fsPath,
    env: commandEnv(folder),
    shell: false
  });
  statusItem.text = "$(radio-tower) Agent Quality";

  collectorProcess.stdout.on("data", (chunk) => output.append(chunk.toString()));
  collectorProcess.stderr.on("data", (chunk) => output.append(chunk.toString()));
  collectorProcess.on("error", (err) => {
    collectorProcess = undefined;
    statusItem.text = "$(pulse) Agent Quality";
    vscode.window.showErrorMessage(`Failed to start Agent Quality collector: ${err.message}`);
  });
  collectorProcess.on("exit", (code, signal) => {
    collectorProcess = undefined;
    statusItem.text = "$(pulse) Agent Quality";
    output.appendLine(`collector exited code=${code} signal=${signal || ""}`);
  });
}

function stopCollector() {
  if (!collectorProcess) {
    vscode.window.showInformationMessage("Agent Quality collector is not running.");
    return;
  }
  collectorProcess.kill();
  collectorProcess = undefined;
  statusItem.text = "$(pulse) Agent Quality";
}

async function reportSummary() {
  const folder = await pickWorkspaceFolder();
  if (!folder) {
    return;
  }
  await runAq(["report", "summary"], folder, { title: "Summary", reveal: true });
  runsProvider.refresh();
}

async function showDashboardRun(context, item, tab) {
  const runId = await resolveRunId(item);
  if (!runId) {
    return;
  }
  DashboardPanel.show(context, runId, tab);
}

async function runAq(args, folder, options) {
  const invocation = aqInvocation(folder);
  const title = options.title || args.join(" ");
  output.show(Boolean(options.reveal));
  output.appendLine("");
  output.appendLine(`[${title}]`);
  output.appendLine(`$ ${quoteArgs([...invocation.commandLine, ...args])}`);
  statusItem.text = "$(sync~spin) Agent Quality";

  return new Promise((resolve) => {
    const child = cp.spawn(invocation.command, [...invocation.prefixArgs, ...args], {
      cwd: folder.uri.fsPath,
      env: commandEnv(folder),
      shell: false
    });
    let stderr = "";
    child.stdout.on("data", (chunk) => output.append(chunk.toString()));
    child.stderr.on("data", (chunk) => {
      const text = chunk.toString();
      stderr += text;
      output.append(text);
    });
    child.on("error", (err) => {
      statusItem.text = "$(pulse) Agent Quality";
      vscode.window.showErrorMessage(`Agent Quality command failed to start: ${err.message}`);
      resolve(undefined);
    });
    child.on("exit", (code) => {
      statusItem.text = collectorProcess ? "$(radio-tower) Agent Quality" : "$(pulse) Agent Quality";
      output.appendLine(`exit=${code}`);
      if (code === 0) {
        resolve(undefined);
      } else {
        const detail = stderr.trim().split(/\r?\n/).slice(-1)[0];
        vscode.window.showErrorMessage(detail || `Agent Quality command exited with code ${code}.`);
        resolve(undefined);
      }
    });
  });
}

class DashboardPanel {
  static currentPanel;

  static show(context, runId, tab) {
    if (DashboardPanel.currentPanel) {
      DashboardPanel.currentPanel.panel.reveal(vscode.ViewColumn.One);
      if (runId) {
        DashboardPanel.currentPanel.selectRun(runId, tab);
      }
      return;
    }
    const panel = vscode.window.createWebviewPanel(
      "agentQualityDashboard",
      "Agent Quality Dashboard",
      vscode.ViewColumn.One,
      {
        enableScripts: true,
        localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, "media")]
      }
    );
    DashboardPanel.currentPanel = new DashboardPanel(context, panel, runId, tab);
  }

  constructor(context, panel, runId, tab) {
    this.context = context;
    this.panel = panel;
    this.panel.webview.html = this.html(runId);
    this.panel.webview.onDidReceiveMessage((message) => this.handleMessage(message), undefined, context.subscriptions);
    this.panel.onDidDispose(() => {
      DashboardPanel.currentPanel = undefined;
    }, undefined, context.subscriptions);
    if (runId && tab) {
      setTimeout(() => this.selectRun(runId, tab), 250);
    }
  }

  selectRun(runId, tab) {
    this.panel.webview.postMessage({ command: "selectRun", run_id: runId, tab });
  }

  html(runId) {
    const mediaRoot = vscode.Uri.joinPath(this.context.extensionUri, "media");
    const htmlPath = vscode.Uri.joinPath(mediaRoot, "dashboard.html");
    const cssUri = this.panel.webview.asWebviewUri(vscode.Uri.joinPath(mediaRoot, "dashboard.css"));
    const jsUri = this.panel.webview.asWebviewUri(vscode.Uri.joinPath(mediaRoot, "dashboard.js"));
    let html = fs.readFileSync(htmlPath.fsPath, "utf8");
    html = html.replace("./dashboard.css", String(cssUri));
    html = html.replace("./dashboard.js", String(jsUri));
    const initial = JSON.stringify(runId || null);
    html = html.replace("</head>", `<script>window.__AGENT_QUALITY_INITIAL_RUN_ID__ = ${initial};</script></head>`);
    return html;
  }

  async handleMessage(message) {
    try {
      if (message.command === "loadRuns") {
        const folder = this.workspaceFolder();
        const runs = await dashboardDbQuery(folder, "runs", {});
        this.reply(message, { command: "runsLoaded", runs });
        return;
      }
      if (message.command === "loadRunDetails") {
        const folder = this.workspaceFolder();
        const details = await dashboardDbQuery(folder, "details", { run_id: message.run_id });
        this.reply(message, { command: "runDetailsLoaded", ...details });
        return;
      }
      if (message.command === "saveReview") {
        const folder = this.workspaceFolder();
        const review = await dashboardDbQuery(folder, "save_review", message);
        runsProvider.refresh();
        this.reply(message, { command: "reviewSaved", review });
        return;
      }
      if (message.command === "openFile") {
        await openDashboardFile(message.path, message.line);
        this.reply(message, { command: "fileOpened", ok: true });
        return;
      }
      if (message.command === "readLog") {
        const result = await readDashboardFile(message.path);
        this.reply(message, { command: "logLoaded", ...result });
        return;
      }
      if (message.command === "openDiff") {
        const folder = this.workspaceFolder();
        await runAq(["diff", message.run_id], folder, { title: `diff ${message.run_id}`, reveal: true });
        this.reply(message, { command: "diffOpened", ok: true });
        return;
      }
      this.replyError(message, `unknown command: ${message.command}`);
    } catch (err) {
      this.replyError(message, err.message || String(err));
    }
  }

  workspaceFolder() {
    const folder = firstWorkspaceFolder();
    if (!folder) {
      throw new Error("Open a workspace folder first.");
    }
    return folder;
  }

  reply(message, payload) {
    this.panel.webview.postMessage({ requestId: message.requestId, ...(payload || {}) });
  }

  replyError(message, error) {
    this.panel.webview.postMessage({ requestId: message.requestId, error });
  }
}

const MAX_FILE_PREVIEW_BYTES = 1_000_000;

const DASHBOARD_DB_SCRIPT = String.raw`
import datetime
import json
import os
import sqlite3
import sys
import uuid

db_path = sys.argv[1]
action = sys.argv[2]
payload = json.loads(sys.argv[3])


def emit(value):
    print(json.dumps(value, sort_keys=True))


def connect():
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(row):
    return {key: row[key] for key in row.keys()}


def json_or_value(value):
    try:
        return json.loads(value)
    except Exception:
        return value


def event_to_dict(row):
    data = row_to_dict(row)
    for key in ("normalized_payload", "source_payload_sanitized", "provider_extensions", "redaction_findings"):
        if data.get(key):
            data[key + "_json"] = json_or_value(data[key])
    return data


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


if action == "runs":
    if not os.path.exists(db_path):
        emit([])
        raise SystemExit(0)
    with connect() as conn:
        emit([row_to_dict(row) for row in conn.execute("SELECT * FROM runs ORDER BY started_at DESC, id DESC")])
elif action == "details":
    if not os.path.exists(db_path):
        raise SystemExit("Agent Quality database does not exist yet.")
    run_id = payload.get("run_id")
    with connect() as conn:
        run = conn.execute("SELECT * FROM runs WHERE id=?", [run_id]).fetchone()
        if not run:
            raise SystemExit("unknown run: " + str(run_id))
        emit({
            "run": row_to_dict(run),
            "artifacts": [
                row_to_dict(row)
                for row in conn.execute("SELECT * FROM artifacts WHERE run_id=? ORDER BY artifact_type, path", [run_id])
            ],
            "verifier_results": [
                row_to_dict(row)
                for row in conn.execute("SELECT * FROM verifier_results WHERE run_id=? ORDER BY started_at, verifier_name", [run_id])
            ],
            "events": [
                event_to_dict(row)
                for row in conn.execute(
                    "SELECT * FROM events WHERE run_id=? ORDER BY COALESCE(sequence_number, rowid), rowid",
                    [run_id],
                )
            ],
            "human_reviews": [
                row_to_dict(row)
                for row in conn.execute(
                    "SELECT * FROM human_reviews WHERE run_id=? ORDER BY reviewed_at DESC, rowid DESC",
                    [run_id],
                )
            ],
        })
elif action == "save_review":
    if not os.path.exists(db_path):
        raise SystemExit("Agent Quality database does not exist yet.")
    run_id = payload.get("run_id")
    outcome = payload.get("outcome")
    if not run_id or not outcome:
        raise SystemExit("run_id and outcome are required")
    reviewed_at = utc_now()
    confidence = payload.get("confidence")
    critical_sequence = payload.get("critical_sequence")
    if critical_sequence in ("", None):
        critical_sequence = None
    else:
        critical_sequence = int(critical_sequence)
    with connect() as conn:
        run = conn.execute("SELECT id FROM runs WHERE id=?", [run_id]).fetchone()
        if not run:
            raise SystemExit("unknown run: " + str(run_id))
        existing = conn.execute(
            "SELECT id FROM human_reviews WHERE run_id=? ORDER BY reviewed_at DESC, rowid DESC LIMIT 1",
            [run_id],
        ).fetchone()
        values = [
            outcome,
            payload.get("severity") or None,
            payload.get("primary_category") or None,
            confidence,
            critical_sequence,
            payload.get("notes") or "",
            reviewed_at,
        ]
        if existing:
            review_id = existing["id"]
            conn.execute(
                """
                UPDATE human_reviews
                SET outcome=?,
                    severity=?,
                    primary_failure_category=?,
                    confidence=?,
                    critical_event_sequence=?,
                    notes=?,
                    reviewed_at=?
                WHERE id=?
                """,
                [*values, review_id],
            )
        else:
            review_id = "rev_" + uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO human_reviews (
                    id,
                    run_id,
                    outcome,
                    code_retention,
                    severity,
                    primary_failure_category,
                    contributing_categories,
                    confidence,
                    critical_event_sequence,
                    notes,
                    reviewed_at
                )
                VALUES (?, ?, ?, NULL, ?, ?, NULL, ?, ?, ?, ?)
                """,
                [review_id, run_id, *values],
            )
        conn.execute("UPDATE runs SET human_status=? WHERE id=?", [outcome, run_id])
        conn.commit()
        emit({
            "id": review_id,
            "run_id": run_id,
            "outcome": outcome,
            "severity": payload.get("severity") or None,
            "primary_failure_category": payload.get("primary_category") or None,
            "confidence": confidence,
            "critical_event_sequence": critical_sequence,
            "notes": payload.get("notes") or "",
            "reviewed_at": reviewed_at,
        })
else:
    raise SystemExit("unknown dashboard action: " + action)
`;

function dashboardDbQuery(folder, action, payload) {
  const dbPath = path.join(agentQualityHome(folder), "quality.sqlite3");
  const python = pythonInvocation();
  return new Promise((resolve, reject) => {
    cp.execFile(python.command, [...python.prefixArgs, "-c", DASHBOARD_DB_SCRIPT, dbPath, action, JSON.stringify(payload || {})], {
      cwd: folder.uri.fsPath,
      env: commandEnv(folder),
      timeout: 15000,
      windowsHide: true,
      maxBuffer: 10 * 1024 * 1024
    }, (err, stdout, stderr) => {
      if (err) {
        reject(new Error((stderr || "").trim() || err.message));
        return;
      }
      try {
        resolve(JSON.parse(stdout || "null"));
      } catch (parseErr) {
        reject(new Error(`dashboard query returned invalid JSON: ${parseErr.message}`));
      }
    });
  });
}

function pythonInvocation() {
  const configured = getConfig().get("pythonPath") || "python3";
  const commandLine = splitCommandLine(configured);
  if (!commandLine.length) {
    return { command: "python3", prefixArgs: [] };
  }
  return {
    command: commandLine[0],
    prefixArgs: commandLine.slice(1)
  };
}

async function openDashboardFile(filePath, line) {
  if (!filePath) {
    throw new Error("missing file path");
  }
  const document = await vscode.workspace.openTextDocument(vscode.Uri.file(filePath));
  const editor = await vscode.window.showTextDocument(document, { preview: false });
  const lineNumber = Number(line);
  if (Number.isFinite(lineNumber) && lineNumber > 0) {
    const position = new vscode.Position(lineNumber - 1, 0);
    editor.selection = new vscode.Selection(position, position);
    editor.revealRange(new vscode.Range(position, position), vscode.TextEditorRevealType.InCenter);
  }
}

async function readDashboardFile(filePath) {
  if (!filePath) {
    throw new Error("missing file path");
  }
  const raw = await fs.promises.readFile(filePath);
  const truncated = raw.length > MAX_FILE_PREVIEW_BYTES;
  return {
    path: filePath,
    content: raw.subarray(0, MAX_FILE_PREVIEW_BYTES).toString("utf8"),
    truncated
  };
}

class RunsProvider {
  constructor() {
    this.emitter = new vscode.EventEmitter();
    this.onDidChangeTreeData = this.emitter.event;
    this.items = [new MessageItem("No runs loaded")];
  }

  refresh() {
    this.load();
  }

  getTreeItem(item) {
    return item;
  }

  getChildren() {
    return this.items;
  }

  dispose() {
    this.emitter.dispose();
  }

  async load() {
    const folder = firstWorkspaceFolder();
    if (!folder) {
      this.items = [new MessageItem("Open a workspace to use Agent Quality")];
      this.emitter.fire();
      return;
    }
    if (!fs.existsSync(path.join(agentQualityHome(folder), "quality.sqlite3"))) {
      this.items = [new MessageItem("No Agent Quality runs yet")];
      this.emitter.fire();
      return;
    }
    try {
      const result = await execAq(["report", "summary"], folder);
      this.items = parseSummary(result.stdout);
    } catch (err) {
      this.items = [new MessageItem(err.message || String(err))];
    }
    this.emitter.fire();
  }
}

class MessageItem extends vscode.TreeItem {
  constructor(label) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.contextValue = "message";
  }
}

class RunItem extends vscode.TreeItem {
  constructor(run) {
    const label = `${run.id} ${run.verifier || "unverified"}`;
    super(label, vscode.TreeItemCollapsibleState.None);
    this.runId = run.id;
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

function parseSummary(stdout) {
  const lines = stdout.split(/\r?\n/);
  const items = [];
  const counts = lines.filter((line) => /^(runs|completed|verified_passed|reviewed|accepted):/.test(line));
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
      }));
    }
  }
  return items.length ? items : [new MessageItem("No Agent Quality runs yet")];
}

function iconForRun(run) {
  if (run.verifier === "passed") {
    return "pass";
  }
  if (run.verifier === "failed" || run.agent === "failed") {
    return "error";
  }
  return "circle-outline";
}

function execAq(args, folder) {
  const invocation = aqInvocation(folder);
  return new Promise((resolve, reject) => {
    cp.execFile(invocation.command, [...invocation.prefixArgs, ...args], {
      cwd: folder.uri.fsPath,
      env: commandEnv(folder),
      timeout: 15000,
      windowsHide: true
    }, (err, stdout, stderr) => {
      if (err) {
        const detail = stderr.trim() || err.message;
        reject(new Error(detail));
        return;
      }
      resolve({ stdout, stderr });
    });
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

function configuredVerifyPath(folder) {
  const configured = getConfig().get("verifyPath");
  if (configured) {
    return path.isAbsolute(configured) ? configured : path.join(folder.uri.fsPath, configured);
  }
  const defaultPath = path.join(folder.uri.fsPath, ".agent-quality", "verify.yaml");
  return fs.existsSync(defaultPath) ? defaultPath : undefined;
}

function commandEnv(folder) {
  const env = {
    ...process.env,
    AGENT_QUALITY_HOME: agentQualityHome(folder)
  };
  const sourceRoot = cliSourceRoot(folder);
  if (sourceRoot) {
    const srcPath = path.join(sourceRoot, "src");
    env.PYTHONPATH = env.PYTHONPATH ? `${srcPath}${path.delimiter}${env.PYTHONPATH}` : srcPath;
  }
  return env;
}

function aqInvocation(folder) {
  const configured = getConfig().get("aqCommand") || "aq";
  const commandLine = splitCommandLine(configured);
  if (commandLine.length === 0) {
    return {
      command: "aq",
      prefixArgs: [],
      commandLine: ["aq"]
    };
  }
  return {
    command: commandLine[0],
    prefixArgs: commandLine.slice(1),
    commandLine
  };
}

function cliSourceRoot(folder) {
  const configured = getConfig().get("cliSourceRoot");
  if (configured) {
    return path.isAbsolute(configured) ? configured : path.join(folder.uri.fsPath, configured);
  }

  for (const candidate of cliSourceRootCandidates(folder.uri.fsPath)) {
    if (hasCliSource(candidate)) {
      return candidate;
    }
  }
  return undefined;
}

function cliSourceRootCandidates(workspacePath) {
  const candidates = [
    workspacePath,
    path.join(workspacePath, "agent-quality"),
    path.join(workspacePath, "markdown_files")
  ];
  try {
    for (const entry of fs.readdirSync(workspacePath, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        candidates.push(path.join(workspacePath, entry.name));
      }
    }
  } catch {
    // Ignore unreadable workspace roots; the explicit candidates above are enough.
  }
  return [...new Set(candidates)];
}

function hasCliSource(candidate) {
  return fs.existsSync(path.join(candidate, "src", "agent_quality", "cli.py"));
}

function splitCommandLine(value) {
  const parts = [];
  let current = "";
  let quote = "";
  let escaped = false;
  for (const char of String(value).trim()) {
    if (escaped) {
      current += char;
      escaped = false;
      continue;
    }
    if (char === "\\") {
      escaped = true;
      continue;
    }
    if (quote) {
      if (char === quote) {
        quote = "";
      } else {
        current += char;
      }
      continue;
    }
    if (char === "'" || char === "\"") {
      quote = char;
      continue;
    }
    if (/\s/.test(char)) {
      if (current) {
        parts.push(current);
        current = "";
      }
      continue;
    }
    current += char;
  }
  if (current) {
    parts.push(current);
  }
  return parts;
}

function agentQualityHome(folder) {
  const configured = getConfig().get("home");
  if (configured) {
    return path.isAbsolute(configured) ? configured : path.join(folder.uri.fsPath, configured);
  }
  return path.join(folder.uri.fsPath, ".agent-quality", "local");
}

async function pickWorkspaceFolder() {
  const folders = vscode.workspace.workspaceFolders || [];
  if (folders.length === 0) {
    vscode.window.showWarningMessage("Open a workspace folder first.");
    return undefined;
  }
  if (folders.length === 1) {
    return folders[0];
  }
  const picked = await vscode.window.showQuickPick(folders.map((folder) => ({
    label: folder.name,
    description: folder.uri.fsPath,
    folder
  })), {
    title: "Select workspace folder"
  });
  return picked && picked.folder;
}

function firstWorkspaceFolder() {
  const folders = vscode.workspace.workspaceFolders || [];
  return folders[0];
}

function getConfig() {
  return vscode.workspace.getConfiguration("agentQuality");
}

function quoteArgs(args) {
  return args.map(shellQuote).join(" ");
}

function shellQuote(value) {
  if (/^[A-Za-z0-9_./:=@+-]+$/.test(value)) {
    return value;
  }
  return `'${String(value).replace(/'/g, "'\\''")}'`;
}

module.exports = {
  activate,
  deactivate
};
