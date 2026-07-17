"use strict";

const cp = require("child_process");
const path = require("path");
const vscode = require("vscode");

const {
  agentQualityHome,
  commandEnv,
  getConfig,
  projectRootPath,
  quoteArgs,
  runUiApi,
  splitCommandLine
} = require("./runtime");
const { mediaHtml } = require("./webview");

class FlywheelRunner {
  constructor(output, statusItem, commandController) {
    this.output = output;
    this.statusItem = statusItem;
    this.commandController = commandController;
    this.processes = new Map();
  }

  dispose() {
    for (const child of this.processes.values()) child.kill();
    this.processes.clear();
  }

  start(folder, runIds, judgeCommand, webview) {
    const workspaceKey = projectRootPath(folder);
    if (this.processes.has(workspaceKey)) {
      throw new Error("A flywheel analysis is already running for this workspace.");
    }
    const commandLine = splitCommandLine(
      getConfig().get("flywheelCommand") || "aq-flywheel"
    );
    if (!commandLine.length) {
      throw new Error("agentQuality.flywheelCommand is empty.");
    }
    const args = [
      ...commandLine.slice(1),
      "analyze",
      "--db",
      path.join(agentQualityHome(folder), "quality.sqlite3"),
      "--min-cluster-size",
      String(getConfig().get("flywheelMinClusterSize") || 2),
      "--judge-command-json",
      JSON.stringify(judgeCommand)
    ];
    for (const runId of runIds) args.push("--run-id", runId);

    this.output.show(true);
    this.output.appendLine("");
    this.output.appendLine(`[Flywheel analysis: ${runIds.length} runs]`);
    const displayArgs = args.map((arg, index) => (
      args[index - 1] === "--judge-command-json" ? "[configured]" : arg
    ));
    this.output.appendLine(`$ ${quoteArgs([commandLine[0], ...displayArgs])}`);
    this.statusItem.text = "$(sync~spin) Agent Quality Flywheel";

    const child = cp.spawn(commandLine[0], args, {
      cwd: workspaceKey,
      env: commandEnv(folder),
      shell: false,
      windowsHide: true
    });
    this.processes.set(workspaceKey, child);
    this.attachProcess(child, workspaceKey, webview);
  }

  attachProcess(child, workspaceKey, webview) {
    let stdoutBuffer = "";
    let stderr = "";
    let finished = false;
    child.stdout.on("data", (chunk) => {
      const text = chunk.toString();
      this.output.append(text);
      stdoutBuffer += text;
      const lines = stdoutBuffer.split(/\r?\n/);
      stdoutBuffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          webview.postMessage({ command: "analysisEvent", event: JSON.parse(line) });
        } catch {
          // Plain-text worker output remains visible in the output channel.
        }
      }
    });
    child.stderr.on("data", (chunk) => {
      const text = chunk.toString();
      stderr += text;
      this.output.append(text);
    });
    child.on("error", (err) => {
      if (finished) return;
      finished = true;
      this.finish(workspaceKey);
      webview.postMessage({ command: "analysisFinished", error: err.message });
      vscode.window.showErrorMessage(`Flywheel analysis failed to start: ${err.message}`);
    });
    child.on("exit", (code) => {
      if (finished) return;
      finished = true;
      this.finish(workspaceKey);
      const error = code === 0
        ? undefined
        : (stderr.trim().split(/\r?\n/).slice(-1)[0] || `worker exited with code ${code}`);
      webview.postMessage({ command: "analysisFinished", code, error });
    });
  }

  finish(workspaceKey) {
    this.processes.delete(workspaceKey);
    this.commandController.resetStatus();
  }
}

class FlywheelPanel {
  static currentPanel;

  static show(context, options) {
    const current = FlywheelPanel.currentPanel;
    if (current && current.workspaceKey === projectRootPath(options.folder)) {
      current.panel.reveal(vscode.ViewColumn.One);
      return current;
    }
    if (current) current.panel.dispose();

    const panel = vscode.window.createWebviewPanel(
      "agentQualityFlywheel",
      "Agent Quality Flywheel",
      vscode.ViewColumn.One,
      {
        enableScripts: true,
        localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, "media")]
      }
    );
    const instance = new FlywheelPanel(context, panel, options);
    FlywheelPanel.currentPanel = instance;
    return instance;
  }

  constructor(context, panel, options) {
    this.panel = panel;
    this.folder = options.folder;
    this.workspaceKey = projectRootPath(this.folder);
    this.runner = options.runner;
    this.openRun = options.openRun;
    this.panel.webview.html = mediaHtml(context, panel, "flywheel");
    this.panel.webview.onDidReceiveMessage(
      (message) => this.handleMessage(message),
      undefined,
      context.subscriptions
    );
    this.panel.onDidDispose(() => {
      if (FlywheelPanel.currentPanel === this) FlywheelPanel.currentPanel = undefined;
    }, undefined, context.subscriptions);
  }

  async handleMessage(message) {
    try {
      if (message.command === "loadCandidates") {
        const runs = await runUiApi(this.folder, "flywheel_candidates");
        this.reply(message, { command: "candidatesLoaded", runs });
        return;
      }
      if (message.command === "loadAnalyses") {
        const analyses = await runUiApi(this.folder, "flywheel_analyses");
        this.reply(message, { command: "analysesLoaded", analyses });
        return;
      }
      if (message.command === "loadAnalysisDetails") {
        const details = await runUiApi(this.folder, "flywheel_analysis_details", {
          analysis_id: message.analysis_id
        });
        this.reply(message, { command: "analysisDetailsLoaded", ...details });
        return;
      }
      if (message.command === "startAnalysis") {
        await this.startAnalysis(message);
        return;
      }
      if (message.command === "copyAnalysisPrompt") {
        const runIds = selectedRunIds(message.run_ids);
        const result = await runUiApi(this.folder, "flywheel_analysis_prompt", {
          run_ids: runIds
        });
        await vscode.env.clipboard.writeText(result.prompt || "");
        vscode.window.showInformationMessage(
          `Copied flywheel analysis prompt for ${result.run_count || 0} run${result.run_count === 1 ? "" : "s"}.`
        );
        this.reply(message, {
          command: "analysisPromptCopied",
          run_count: result.run_count || 0,
          character_count: result.character_count || 0
        });
        return;
      }
      if (message.command === "openRun") {
        if (typeof message.run_id !== "string" || !message.run_id) {
          throw new Error("missing run ID");
        }
        this.openRun(message.run_id);
        this.reply(message, { command: "runOpened", ok: true });
        return;
      }
      this.replyError(message, `unknown command: ${message.command}`);
    } catch (err) {
      this.replyError(message, err.message || String(err));
    }
  }

  async startAnalysis(message) {
    const runIds = selectedRunIds(message.run_ids);
    const judgeCommand = getConfig().get("flywheelJudgeCommand");
    if (!Array.isArray(judgeCommand) || !judgeCommand.length
        || judgeCommand.some((arg) => typeof arg !== "string" || !arg)) {
      throw new Error(
        "Configure agentQuality.flywheelJudgeCommand as a non-empty argument array first."
      );
    }
    const confirmation = await vscode.window.showWarningMessage(
      `Analyze ${runIds.length} run${runIds.length === 1 ? "" : "s"} with ${path.basename(judgeCommand[0])}?`,
      {
        modal: true,
        detail: "Persisted payloads are redacted again before each prompt is sent to the external judge command."
      },
      "Run Analysis"
    );
    if (confirmation !== "Run Analysis") {
      this.reply(message, { command: "analysisStartCancelled", started: false });
      return;
    }
    this.runner.start(this.folder, runIds, judgeCommand, this.panel.webview);
    this.reply(message, { command: "analysisStarted", started: true });
  }

  reply(message, payload) {
    this.panel.webview.postMessage({ requestId: message.requestId, ...payload });
  }

  replyError(message, error) {
    this.panel.webview.postMessage({ requestId: message.requestId, error });
  }
}

function selectedRunIds(value) {
  const runIds = Array.isArray(value)
    ? [...new Set(value.filter((item) => typeof item === "string" && item))]
    : [];
  if (!runIds.length) throw new Error("Select at least one run.");
  return runIds;
}

module.exports = { FlywheelPanel, FlywheelRunner };
