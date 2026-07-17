"use strict";

const vscode = require("vscode");

const { CommandController } = require("./commands");
const { DashboardPanel } = require("./dashboard-panel");
const { FlywheelPanel, FlywheelRunner } = require("./flywheel-panel");
const { RunsProvider, resolveRunId } = require("./runs-tree");
const { pickWorkspaceFolder } = require("./runtime");

let commands;
let flywheelRunner;

function activate(context) {
  const output = vscode.window.createOutputChannel("Agent Quality");
  const statusItem = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Left,
    100
  );
  statusItem.text = "$(pulse) Agent Quality";
  statusItem.command = "agentQuality.reportSummary";
  statusItem.tooltip = "Show Agent Quality summary";
  statusItem.show();

  const runsProvider = new RunsProvider();
  const refreshRuns = () => runsProvider.refresh();
  commands = new CommandController(output, statusItem, refreshRuns);
  flywheelRunner = new FlywheelRunner(output, statusItem, commands);

  context.subscriptions.push(
    output,
    statusItem,
    runsProvider,
    commands,
    flywheelRunner,
    vscode.window.registerTreeDataProvider("agentQuality.runs", runsProvider)
  );

  register(context, "agentQuality.initProject", () => commands.initProject());
  register(context, "agentQuality.runPrompt", () => commands.runPrompt());
  register(context, "agentQuality.runSelection", () => commands.runSelection());
  register(context, "agentQuality.installUserHooks", () => commands.installUserHooks());
  register(context, "agentQuality.hookStatus", () => commands.hookStatus());
  register(context, "agentQuality.startCollector", () => commands.startCollector());
  register(context, "agentQuality.stopCollector", () => commands.stopCollector());
  register(context, "agentQuality.reportSummary", () => commands.reportSummary());
  register(context, "agentQuality.refreshRuns", refreshRuns);
  register(context, "agentQuality.showDashboard", async () => {
    const folder = await pickWorkspaceFolder();
    if (folder) showDashboard(context, runsProvider, folder);
  });
  register(context, "agentQuality.showFlywheel", async () => {
    const folder = await pickWorkspaceFolder();
    if (folder) showFlywheel(context, runsProvider, folder);
  });
  register(context, "agentQuality.showRun", (item) => (
    showDashboardRun(context, runsProvider, item, "overview")
  ));
  register(context, "agentQuality.diffRun", (item) => (
    showDashboardRun(context, runsProvider, item, "artifacts")
  ));
  register(context, "agentQuality.traceRun", (item) => (
    showDashboardRun(context, runsProvider, item, "timeline")
  ));
  register(context, "agentQuality.reviewRun", (item) => (
    showDashboardRun(context, runsProvider, item, "review")
  ));

  runsProvider.refresh();
}

function deactivate() {
  if (flywheelRunner) flywheelRunner.dispose();
  if (commands) commands.dispose();
  flywheelRunner = undefined;
  commands = undefined;
}

function register(context, command, handler) {
  context.subscriptions.push(vscode.commands.registerCommand(command, handler));
}

function showDashboard(context, runsProvider, folder, runId, tab) {
  return DashboardPanel.show(context, {
    folder,
    runId,
    tab,
    refreshRuns: () => runsProvider.refresh(),
    runCommand: (args, commandFolder, options) => (
      commands.runAq(args, commandFolder, options)
    )
  });
}

function showFlywheel(context, runsProvider, folder) {
  return FlywheelPanel.show(context, {
    folder,
    runner: flywheelRunner,
    openRun: (runId) => showDashboard(
      context,
      runsProvider,
      folder,
      runId,
      "overview"
    )
  });
}

async function showDashboardRun(context, runsProvider, item, tab) {
  const runId = await resolveRunId(item);
  if (!runId) return;
  const folder = item && item.workspaceFolder
    ? item.workspaceFolder
    : await pickWorkspaceFolder();
  if (folder) showDashboard(context, runsProvider, folder, runId, tab);
}

module.exports = { activate, deactivate };
