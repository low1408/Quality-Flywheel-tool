"use strict";

const vscode = require("vscode");

const { projectRootPath, runUiApi } = require("./runtime");
const { mediaHtml, openTextFile } = require("./webview");

const DELETE_CHAT_CONFIRMATION = "Delete Chat";

class DashboardPanel {
  static currentPanel;

  static show(context, options) {
    const { folder, runId, tab } = options;
    const current = DashboardPanel.currentPanel;
    if (current && current.workspaceKey === projectRootPath(folder)) {
      current.panel.reveal(vscode.ViewColumn.One);
      if (runId) current.selectRun(runId, tab);
      return current;
    }
    if (current) current.panel.dispose();

    const panel = vscode.window.createWebviewPanel(
      "agentQualityDashboard",
      "Agent Quality Dashboard",
      vscode.ViewColumn.One,
      {
        enableScripts: true,
        localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, "media")]
      }
    );
    const instance = new DashboardPanel(context, panel, options);
    DashboardPanel.currentPanel = instance;
    return instance;
  }

  constructor(context, panel, options) {
    this.panel = panel;
    this.folder = options.folder;
    this.workspaceKey = projectRootPath(this.folder);
    this.refreshRuns = options.refreshRuns;
    this.runCommand = options.runCommand;
    this.webviewReady = false;
    this.pendingSelection = undefined;
    this.panel.webview.html = mediaHtml(context, panel, "dashboard");
    this.panel.webview.onDidReceiveMessage(
      (message) => this.handleMessage(message),
      undefined,
      context.subscriptions
    );
    this.panel.onDidDispose(() => {
      if (DashboardPanel.currentPanel === this) {
        DashboardPanel.currentPanel = undefined;
      }
    }, undefined, context.subscriptions);
    if (options.runId) this.selectRun(options.runId, options.tab);
  }

  selectRun(runId, tab = "overview") {
    this.pendingSelection = { command: "selectRun", run_id: runId, tab };
    this.flushSelection();
  }

  flushSelection() {
    if (!this.webviewReady || !this.pendingSelection) return;
    const selection = this.pendingSelection;
    this.pendingSelection = undefined;
    this.panel.webview.postMessage(selection);
  }

  async handleMessage(message) {
    try {
      if (message.command === "ready") {
        this.webviewReady = true;
        this.flushSelection();
        return;
      }
      if (message.command === "loadRuns") {
        const runs = await runUiApi(this.folder, "runs");
        this.reply(message, { command: "runsLoaded", runs });
        return;
      }
      if (message.command === "loadSessions") {
        const sessions = await runUiApi(this.folder, "sessions");
        this.reply(message, { command: "sessionsLoaded", sessions });
        return;
      }
      if (message.command === "loadRunDetails") {
        const details = await runUiApi(this.folder, "details", {
          run_id: message.run_id
        });
        this.reply(message, { command: "runDetailsLoaded", ...details });
        return;
      }
      if (message.command === "loadSessionDetails") {
        const details = await runUiApi(this.folder, "session_details", {
          session_id: message.session_id
        });
        this.reply(message, { command: "sessionDetailsLoaded", ...details });
        return;
      }
      if (message.command === "saveReview") {
        const review = await runUiApi(this.folder, "save_review", message);
        this.refreshRuns();
        this.reply(message, { command: "reviewSaved", review });
        return;
      }
      if (message.command === "deleteChat") {
        await this.deleteChat(message);
        return;
      }
      if (message.command === "copyText") {
        const text = requiredString(message.text, "nothing to copy");
        await vscode.env.clipboard.writeText(text);
        const label = typeof message.label === "string" && message.label
          ? message.label
          : "text";
        vscode.window.showInformationMessage(`Copied ${label} to clipboard.`);
        this.reply(message, { command: "textCopied", ok: true });
        return;
      }
      if (message.command === "openFile") {
        const file = await this.authorizedFile(message.path);
        await openTextFile(file.path, message.line);
        this.reply(message, { command: "fileOpened", ok: true });
        return;
      }
      if (message.command === "readLog") {
        const file = await this.authorizedFile(message.path);
        this.reply(message, { command: "logLoaded", ...file });
        return;
      }
      if (message.command === "openDiff") {
        const runId = requiredString(message.run_id, "missing run ID");
        await this.runCommand(["diff", runId], this.folder, {
          title: `diff ${runId}`,
          reveal: true
        });
        this.reply(message, { command: "diffOpened", ok: true });
        return;
      }
      this.replyError(message, `unknown command: ${message.command}`);
    } catch (err) {
      this.replyError(message, err.message || String(err));
    }
  }

  async deleteChat(message) {
    const chatId = requiredString(message.chat_id, "missing chat ID");
    const confirmation = await vscode.window.showWarningMessage(
      "Permanently delete this chat from Agent Quality?",
      {
        modal: true,
        detail: "This removes the chat, its runs, events, reviews, verifier results, and database artifact references. Referenced files are not deleted."
      },
      DELETE_CHAT_CONFIRMATION
    );
    if (confirmation !== DELETE_CHAT_CONFIRMATION) {
      this.reply(message, { command: "chatDeletionCancelled", deleted: false });
      return;
    }
    const result = await runUiApi(this.folder, "delete_chat", { chat_id: chatId });
    this.refreshRuns();
    this.reply(message, { command: "chatDeleted", ...result });
  }

  authorizedFile(filePath) {
    return runUiApi(this.folder, "read_file", {
      path: requiredString(filePath, "missing file path")
    });
  }

  reply(message, payload) {
    this.panel.webview.postMessage({ requestId: message.requestId, ...payload });
  }

  replyError(message, error) {
    this.panel.webview.postMessage({ requestId: message.requestId, error });
  }
}

function requiredString(value, message) {
  if (typeof value !== "string" || !value.trim()) throw new Error(message);
  return value.trim();
}

module.exports = { DashboardPanel };
