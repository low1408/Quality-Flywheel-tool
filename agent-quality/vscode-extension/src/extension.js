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
  register(context, "agentQuality.installAntigravityHooks", installAntigravityHooks);
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
  const repo = projectRootPath(folder);
  await runAq(["init", "--repo", repo], folder, { title: "Initialize project" });
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
  const args = ["run", "--repo", projectRootPath(folder)];
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
  await runAq(["install-codex-hooks", "--repo", projectRootPath(folder), "--python", pythonPath], folder, {
    title: "Install Codex hooks"
  });
}

async function installAntigravityHooks() {
  const folder = await pickWorkspaceFolder();
  if (!folder) {
    return;
  }
  const pythonPath = getConfig().get("pythonPath") || "python3";
  await runAq(["install-antigravity-hooks", "--repo", projectRootPath(folder), "--python", pythonPath], folder, {
    title: "Install Antigravity hooks"
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
    cwd: projectRootPath(folder),
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
      cwd: projectRootPath(folder),
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
      if (message.command === "loadSessions") {
        const folder = this.workspaceFolder();
        const sessions = await dashboardDbQuery(folder, "sessions", {});
        this.reply(message, { command: "sessionsLoaded", sessions });
        return;
      }
      if (message.command === "loadRunDetails") {
        const folder = this.workspaceFolder();
        const details = await dashboardDbQuery(folder, "details", { run_id: message.run_id });
        this.reply(message, { command: "runDetailsLoaded", ...details });
        return;
      }
      if (message.command === "loadSessionDetails") {
        const folder = this.workspaceFolder();
        const details = await dashboardDbQuery(folder, "session_details", { session_id: message.session_id });
        this.reply(message, { command: "sessionDetailsLoaded", ...details });
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
import hashlib
import json
import os
import re
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


def sha256_text(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


MARKDOWN_FILE_LINK_RE = re.compile(r"\[[^\]]+\]\((/[^)\n]+?)(?::(\d+))?\)")


def assistant_output(event_name, hook):
    if event_name not in ("Stop", "AssistantMessage", "AgentMessage") or not isinstance(hook, dict):
        return None
    for key in (
        "last_assistant_message",
        "assistant_message",
        "assistantMessage",
        "assistant_output",
        "assistantOutput",
        "final_response",
        "finalResponse",
        "response",
        "output",
        "message",
        "text",
        "content",
    ):
        value = hook.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def file_links(hook, text=None):
    links = []
    seen = set()

    def add(path, line=None, label=None):
        if not isinstance(path, str):
            return
        path = strip_line_suffix(path.strip())
        if not path.startswith("/"):
            return
        key = (path, line)
        if key in seen:
            return
        seen.add(key)
        item = {"path": path}
        if line is not None:
            item["line"] = line
        if label:
            item["label"] = label
        links.append(item)

    for value in (text, hook.get("prompt") if isinstance(hook, dict) else None):
        if not isinstance(value, str):
            continue
        for match in MARKDOWN_FILE_LINK_RE.finditer(value):
            add(match.group(1), int(match.group(2)) if match.group(2) else None)
    if isinstance(hook, dict):
        for key in ("path", "file", "file_path", "filePath"):
            add(hook.get(key))
        for key in ("files", "file_paths", "filePaths"):
            values = hook.get(key)
            if isinstance(values, list):
                for value in values:
                    if isinstance(value, str):
                        add(value)
                    elif isinstance(value, dict):
                        add(value.get("path") or value.get("file"), value.get("line") or value.get("line_number"))
    return links


def hook_artifacts(hook):
    artifacts = []
    seen = set()

    def add(path, artifact_type):
        if not isinstance(path, str):
            return
        path = strip_line_suffix(path.strip())
        if not path.startswith("/") or path in seen:
            return
        seen.add(path)
        artifacts.append({"artifact_type": artifact_type, "path": path})

    if isinstance(hook, dict):
        add(hook.get("transcript_path") or hook.get("transcriptPath"), "transcript")
        for key in ("artifact_path", "artifactPath", "log_path", "logPath"):
            add(hook.get(key), "hook_artifact")
        values = hook.get("artifacts") or hook.get("artifact_paths") or hook.get("artifactPaths")
        if isinstance(values, list):
            for value in values:
                if isinstance(value, str):
                    add(value, "hook_artifact")
                elif isinstance(value, dict):
                    add(value.get("path") or value.get("file"), value.get("artifact_type") or value.get("type") or "hook_artifact")
    return artifacts


def strip_line_suffix(path):
    if not isinstance(path, str) or ":" not in path:
        return path
    prefix, suffix = path.rsplit(":", 1)
    return prefix if suffix.isdigit() else path


def hook_payload(row):
    extensions = json_or_value(row["provider_extensions"])
    if not isinstance(extensions, dict):
        return None
    hook = extensions.get("openai.codex.hook")
    return hook if isinstance(hook, dict) else None


def event_artifact_items(row):
    items = []
    payload = json_or_value(row["normalized_payload"])
    if isinstance(payload, dict):
        if isinstance(payload.get("path"), str) and payload.get("path"):
            items.append({"artifact_type": "event_path", "path": payload["path"]})
        for link in payload.get("file_links") or []:
            if isinstance(link, dict) and isinstance(link.get("path"), str):
                item = {"artifact_type": "linked_file"}
                item.update(link)
                items.append(item)
        for artifact in payload.get("artifacts") or []:
            if isinstance(artifact, dict) and isinstance(artifact.get("path"), str):
                items.append(artifact)
    hook = hook_payload(row)
    if hook:
        items.extend(hook_artifacts(hook))
        output = assistant_output(row["source_event_type"], hook)
        for link in file_links(hook, output):
            item = {"artifact_type": "linked_file"}
            item.update(link)
            items.append(item)
    return items


def event_artifacts(conn, run_id):
    artifacts = []
    seen = set()
    for row in conn.execute("SELECT * FROM events WHERE run_id=? ORDER BY rowid", [run_id]).fetchall():
        for item in event_artifact_items(row):
            path = item.get("path")
            if not isinstance(path, str) or not path or path in seen:
                continue
            seen.add(path)
            size = os.path.getsize(path) if os.path.isfile(path) else None
            artifacts.append({
                "id": "event_artifact_" + sha256_text(path)[:16],
                "run_id": run_id,
                "artifact_type": item.get("artifact_type") or "linked_file",
                "path": path,
                "line": item.get("line"),
                "sha256": None,
                "size_bytes": size,
            })
    return artifacts


def agent_outputs(conn, run_id):
    outputs = []
    rows = conn.execute(
        "SELECT * FROM events WHERE run_id=? ORDER BY COALESCE(sequence_number, rowid), rowid",
        [run_id],
    ).fetchall()
    for row in rows:
        payload = json_or_value(row["normalized_payload"])
        hook = hook_payload(row)
        text = None
        links = []
        if isinstance(payload, dict):
            text = payload.get("assistant_output")
            links = payload.get("file_links") if isinstance(payload.get("file_links"), list) else []
        if not text and hook:
            text = assistant_output(row["source_event_type"], hook)
            links = file_links(hook, text)
        if text:
            outputs.append({
                "event_id": row["id"],
                "sequence_number": row["sequence_number"],
                "occurred_at": row["occurred_at"] or row["observed_at"],
                "text": str(text),
                "file_links": links,
            })
    return outputs


def reasoning_trace(conn, run_id):
    trace = []
    rows = conn.execute(
        "SELECT * FROM events WHERE run_id=? ORDER BY COALESCE(occurred_at, observed_at), rowid",
        [run_id],
    ).fetchall()
    for row in rows:
        event_payload = json_or_value(row["normalized_payload"])
        if not isinstance(event_payload, dict) or not event_payload.get("reasoning"):
            continue
        trace.append({
            "event_id": row["id"],
            "occurred_at": row["occurred_at"] or row["observed_at"],
            "kind": event_payload.get("reasoning_kind") or "summary",
            "text": str(event_payload["reasoning"]),
        })
    return trace


def tool_calls(conn, run_id):
    calls = []
    by_id = {}
    rows = conn.execute(
        "SELECT * FROM events WHERE run_id=? ORDER BY COALESCE(sequence_number, rowid), rowid",
        [run_id],
    ).fetchall()
    for row in rows:
        event_payload = json_or_value(row["normalized_payload"])
        event_payload = event_payload if isinstance(event_payload, dict) else {}
        hook = hook_payload(row) or {}
        source_type = str(row["source_event_type"] or "")
        is_started = source_type == "PreToolUse" or row["event_type"] == "agent.tool.started"
        is_completed = source_type == "PostToolUse" or row["event_type"] == "agent.tool.completed"
        if not is_started and not is_completed:
            continue
        call_id = event_payload.get("tool_call_id") or hook.get("tool_use_id") or hook.get("call_id")
        tool_name = event_payload.get("tool_name") or hook.get("tool_name") or hook.get("toolName")
        tool_category = row["tool_category"] or event_payload.get("tool_category")
        if isinstance(tool_name, str) and tool_name.lower().startswith("mcp__"):
            tool_category = "mcp"
        key = str(call_id) if call_id else str(tool_name) + ":" + str(row["id"])
        call = by_id.get(key)
        if call is None:
            call = {
                "event_id": row["id"],
                "call_id": call_id,
                "occurred_at": row["occurred_at"] or row["observed_at"],
                "tool_name": tool_name or row["tool_category"] or "tool",
                "tool_category": tool_category,
                "status": row["status"],
                "input": event_payload.get("tool_input", hook.get("tool_input", hook.get("toolInput"))),
                "output": None,
            }
            by_id[key] = call
            calls.append(call)
        elif call.get("input") is None:
            call["input"] = event_payload.get("tool_input", hook.get("tool_input", hook.get("toolInput")))
        if is_completed:
            call["status"] = row["status"] or "completed"
            call["output"] = event_payload.get(
                "tool_output",
                hook.get("tool_response", hook.get("toolResponse")),
            )
    return calls


def backfill_session_event_run_ids(conn):
    rows = conn.execute(
        """
        SELECT rowid, run_id, session_id
        FROM events
        WHERE source_event_type='UserPromptSubmit'
          AND session_id IS NOT NULL
          AND run_id IS NOT NULL
        ORDER BY session_id, rowid
        """
    ).fetchall()
    for row in rows:
        next_row = conn.execute(
            """
            SELECT MIN(rowid) AS rowid
            FROM events
            WHERE session_id=?
              AND source_event_type='UserPromptSubmit'
              AND rowid>?
            """,
            [row["session_id"], row["rowid"]],
        ).fetchone()
        next_rowid = next_row["rowid"] if next_row else None
        if next_rowid is None:
            conn.execute(
                "UPDATE events SET run_id=? WHERE session_id=? AND run_id IS NULL AND rowid>=?",
                [row["run_id"], row["session_id"], row["rowid"]],
            )
        else:
            conn.execute(
                "UPDATE events SET run_id=? WHERE session_id=? AND run_id IS NULL AND rowid>=? AND rowid<?",
                [row["run_id"], row["session_id"], row["rowid"], next_rowid],
            )


def backfill_prompt_runs(conn):
    rows = conn.execute(
        """
        SELECT *
        FROM events
        WHERE source_event_type='UserPromptSubmit'
          AND (run_id IS NOT NULL OR source_payload_sanitized IS NOT NULL)
        ORDER BY COALESCE(occurred_at, observed_at), rowid
        """
    ).fetchall()
    for row in rows:
        existing_run_id = row["run_id"]
        if existing_run_id and conn.execute("SELECT id FROM runs WHERE id=?", [existing_run_id]).fetchone():
            continue
        payload = json_or_value(row["source_payload_sanitized"])
        if not isinstance(payload, dict):
            continue
        hook = (((payload.get("extensions") or {}).get("openai.codex.hook")) or {})
        if not isinstance(hook, dict):
            continue
        prompt = str(hook.get("prompt") or "").strip()
        if not prompt:
            continue
        run_id = existing_run_id or "run_" + sha256_text(row["id"])[:32]
        if conn.execute("SELECT id FROM runs WHERE id=?", [run_id]).fetchone():
            continue
        session_id = row["session_id"] or hook.get("session_id")
        started_at = row["occurred_at"] or row["observed_at"]
        repo_path = str(hook.get("cwd") or "")
        if session_id:
            conn.execute(
                """
                INSERT OR IGNORE INTO sessions (
                    id, repository_path, repository_remote_hash, started_at, ended_at, final_outcome, task_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [session_id, repo_path or "unknown", None, started_at, None, None, prompt[:240]],
            )
            turn_number = conn.execute(
                "SELECT COALESCE(MAX(turn_number), 0) + 1 AS n FROM runs WHERE session_id=?",
                [session_id],
            ).fetchone()["n"]
        else:
            turn_number = 1
        conn.execute(
            """
            INSERT OR IGNORE INTO runs (
                id, session_id, turn_number, prompt, prompt_hash, repository_path, base_commit,
                resulting_commit, model, agent_adapter, agent_version, wrapper_version,
                codex_config_hash, agents_md_hash, verifier_version, started_at, completed_at,
                duration_ms, agent_status, verifier_status, human_status, lifecycle_status,
                input_tokens, cached_input_tokens, output_tokens
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL, NULL, NULL, NULL, NULL, ?, NULL, NULL, ?, ?, ?, ?, NULL, NULL, NULL)
            """,
            [
                run_id,
                session_id,
                turn_number,
                prompt,
                sha256_text(prompt),
                repo_path or "unknown",
                "unknown",
                hook.get("model"),
                "codex-hooks",
                started_at,
                "prompt_submitted",
                "unverified",
                "not_reviewed",
                "still_open",
            ],
        )
    backfill_session_event_run_ids(conn)


if action == "runs":
    if not os.path.exists(db_path):
        emit([])
        raise SystemExit(0)
    with connect() as conn:
        backfill_prompt_runs(conn)
        conn.commit()
        emit([row_to_dict(row) for row in conn.execute("SELECT * FROM runs ORDER BY started_at DESC, id DESC")])
elif action == "sessions":
    if not os.path.exists(db_path):
        emit([])
        raise SystemExit(0)
    with connect() as conn:
        backfill_prompt_runs(conn)
        conn.commit()
        sql = """
        SELECT
            s.id AS id,
            s.repository_path AS repository_path,
            s.started_at AS started_at,
            s.ended_at AS ended_at,
            s.final_outcome AS final_outcome,
            s.task_summary AS task_summary,
            1 AS is_session,
            (SELECT COUNT(*) FROM runs r WHERE r.session_id = s.id) AS turn_count,
            (SELECT model FROM runs r WHERE r.session_id = s.id ORDER BY turn_number DESC LIMIT 1) AS model,
            (SELECT agent_adapter FROM runs r WHERE r.session_id = s.id ORDER BY turn_number DESC LIMIT 1) AS agent_adapter,
            (SELECT agent_status FROM runs r WHERE r.session_id = s.id ORDER BY turn_number DESC LIMIT 1) AS agent_status,
            (SELECT verifier_status FROM runs r WHERE r.session_id = s.id ORDER BY turn_number DESC LIMIT 1) AS verifier_status,
            (SELECT human_status FROM runs r WHERE r.session_id = s.id ORDER BY turn_number DESC LIMIT 1) AS human_status
        FROM sessions s
        
        UNION ALL
        
        SELECT
            r.id AS id,
            r.repository_path AS repository_path,
            r.started_at AS started_at,
            r.completed_at AS ended_at,
            r.verifier_status AS final_outcome,
            r.prompt AS task_summary,
            0 AS is_session,
            1 AS turn_count,
            r.model AS model,
            r.agent_adapter AS agent_adapter,
            r.agent_status AS agent_status,
            r.verifier_status AS verifier_status,
            r.human_status AS human_status
        FROM runs r
        WHERE r.session_id IS NULL OR r.session_id = ''
        
        ORDER BY started_at DESC, id DESC
        """
        emit([row_to_dict(row) for row in conn.execute(sql)])
elif action == "details":
    if not os.path.exists(db_path):
        raise SystemExit("Agent Quality database does not exist yet.")
    run_id = payload.get("run_id")
    with connect() as conn:
        backfill_prompt_runs(conn)
        conn.commit()
        run = conn.execute("SELECT * FROM runs WHERE id=?", [run_id]).fetchone()
        if not run:
            raise SystemExit("unknown run: " + str(run_id))
        emit({
            "run": row_to_dict(run),
            "artifacts": [
                row_to_dict(row)
                for row in conn.execute("SELECT * FROM artifacts WHERE run_id=? ORDER BY artifact_type, path", [run_id])
            ] + event_artifacts(conn, run_id),
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
            "agent_outputs": agent_outputs(conn, run_id),
            "reasoning_trace": reasoning_trace(conn, run_id),
            "tool_calls": tool_calls(conn, run_id),
            "human_reviews": [
                row_to_dict(row)
                for row in conn.execute(
                    "SELECT * FROM human_reviews WHERE run_id=? ORDER BY reviewed_at DESC, rowid DESC",
                    [run_id],
                )
            ],
        })
elif action == "session_details":
    if not os.path.exists(db_path):
        raise SystemExit("Agent Quality database does not exist yet.")
    session_id = payload.get("session_id")
    with connect() as conn:
        backfill_prompt_runs(conn)
        conn.commit()
        session_row = conn.execute("SELECT * FROM sessions WHERE id=?", [session_id]).fetchone()
        if session_row:
            session_dict = row_to_dict(session_row)
            runs = conn.execute("SELECT * FROM runs WHERE session_id=? ORDER BY turn_number ASC, started_at ASC", [session_id]).fetchall()
        else:
            run_row = conn.execute("SELECT * FROM runs WHERE id=?", [session_id]).fetchone()
            if not run_row:
                raise SystemExit("unknown session or run: " + str(session_id))
            run_dict = row_to_dict(run_row)
            session_dict = {
                "id": session_id,
                "repository_path": run_dict["repository_path"],
                "repository_remote_hash": None,
                "started_at": run_dict["started_at"],
                "ended_at": run_dict["completed_at"],
                "final_outcome": run_dict["verifier_status"],
                "task_summary": run_dict["prompt"][:240] if run_dict["prompt"] else ""
            }
            runs = [run_row]
        
        turns_details = []
        all_artifacts = []
        all_verifier_results = []
        all_events = []
        
        for run in runs:
            r_id = run["id"]
            artifacts = [
                row_to_dict(row)
                for row in conn.execute("SELECT * FROM artifacts WHERE run_id=? ORDER BY artifact_type, path", [r_id])
            ] + event_artifacts(conn, r_id)
            
            verifier_results = [
                row_to_dict(row)
                for row in conn.execute("SELECT * FROM verifier_results WHERE run_id=? ORDER BY started_at, verifier_name", [r_id])
            ]
            
            events = [
                event_to_dict(row)
                for row in conn.execute(
                    "SELECT * FROM events WHERE run_id=? ORDER BY COALESCE(sequence_number, rowid), rowid",
                    [r_id],
                )
            ]
            
            outputs = agent_outputs(conn, r_id)
            trace = reasoning_trace(conn, r_id)
            calls = tool_calls(conn, r_id)
            
            human_reviews = [
                row_to_dict(row)
                for row in conn.execute(
                    "SELECT * FROM human_reviews WHERE run_id=? ORDER BY reviewed_at DESC, rowid DESC",
                    [r_id],
                )
            ]
            
            turns_details.append({
                "run": row_to_dict(run),
                "artifacts": artifacts,
                "verifier_results": verifier_results,
                "events": events,
                "agent_outputs": outputs,
                "reasoning_trace": trace,
                "tool_calls": calls,
                "human_reviews": human_reviews
            })
            
            all_artifacts.extend(artifacts)
            all_verifier_results.extend(verifier_results)
            all_events.extend(events)
            
        emit({
            "session": session_dict,
            "turns": turns_details,
            "all_artifacts": all_artifacts,
            "all_verifier_results": all_verifier_results,
            "all_events": all_events
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
      cwd: projectRootPath(folder),
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
      cwd: projectRootPath(folder),
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
  const repo = projectRootPath(folder);
  const configured = getConfig().get("verifyPath");
  if (configured) {
    return path.isAbsolute(configured) ? configured : path.join(repo, configured);
  }
  const defaultPath = path.join(repo, ".agent-quality", "verify.yaml");
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
  const repo = projectRootPath(folder);
  if (configured) {
    return path.isAbsolute(configured) ? configured : path.join(repo, configured);
  }

  for (const candidate of cliSourceRootCandidates(repo)) {
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
  const repo = projectRootPath(folder);
  if (configured) {
    return path.isAbsolute(configured) ? configured : path.join(repo, configured);
  }
  return path.join(repo, ".agent-quality", "local");
}

function projectRootPath(folder) {
  const workspacePath = folder.uri.fsPath;
  let current = path.resolve(workspacePath);
  try {
    if (fs.existsSync(current) && fs.statSync(current).isFile()) {
      current = path.dirname(current);
    }
  } catch {
    return current;
  }
  while (true) {
    if (fs.existsSync(path.join(current, ".git"))) {
      return current;
    }
    const parent = path.dirname(current);
    if (parent === current) {
      return path.resolve(workspacePath);
    }
    current = parent;
  }
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
