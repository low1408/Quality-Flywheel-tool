"use strict";

const cp = require("child_process");
const vscode = require("vscode");

const {
  aqInvocation,
  commandEnv,
  commandWorkingDirectory,
  configuredVerifyPath,
  getConfig,
  optionalWorkspaceFolder,
  pickWorkspaceFolder,
  projectRootPath,
  quoteArgs
} = require("./runtime");

class CommandController {
  constructor(output, statusItem, refreshRuns) {
    this.output = output;
    this.statusItem = statusItem;
    this.refreshRuns = refreshRuns;
    this.collectorProcess = undefined;
  }

  collectorRunning() {
    return Boolean(this.collectorProcess);
  }

  dispose() {
    if (this.collectorProcess) {
      this.collectorProcess.kill();
      this.collectorProcess = undefined;
    }
  }

  async initProject() {
    const folder = await pickWorkspaceFolder();
    if (!folder) return;
    await this.runAq(["init", "--repo", projectRootPath(folder)], folder, {
      title: "Initialize project"
    });
    this.refreshRuns();
  }

  async runPrompt() {
    const folder = await pickWorkspaceFolder();
    if (!folder) return;
    const prompt = await vscode.window.showInputBox({
      title: "Agent Quality: Run Prompt",
      prompt: "Prompt to pass to aq run",
      ignoreFocusOut: true
    });
    if (prompt) {
      await this.runMeasuredPrompt(folder, prompt);
    }
  }

  async runSelection() {
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
    const folder = vscode.workspace.getWorkspaceFolder(editor.document.uri)
      || await pickWorkspaceFolder();
    if (folder) {
      await this.runMeasuredPrompt(folder, selection);
    }
  }

  async runMeasuredPrompt(folder, prompt) {
    const cfg = getConfig();
    const args = ["run", "--repo", projectRootPath(folder)];
    const verifyPath = configuredVerifyPath(folder);
    if (verifyPath) args.push("--verify", verifyPath);
    if (cfg.get("allowDirtyRuns")) args.push("--allow-dirty");
    const model = cfg.get("model");
    if (model) args.push("--model", model);
    args.push(prompt);

    await this.runAq(args, folder, {
      title: "Run prompt",
      reveal: true,
      displayArgs: [...args.slice(0, -1), "[prompt]"]
    });
    this.refreshRuns();
  }

  async installUserHooks() {
    const folder = optionalWorkspaceFolder();
    const args = ["hooks", "install", "--provider", "all"];
    const pythonPath = getConfig().get("pythonPath");
    if (pythonPath) args.push("--python", pythonPath);
    await this.runAq(args, folder, {
      title: "Install user hooks",
      reveal: true,
      projectHome: false
    });
  }

  async hookStatus() {
    await this.runAq(
      ["hooks", "status", "--provider", "all"],
      optionalWorkspaceFolder(),
      { title: "User hook status", reveal: true, projectHome: false }
    );
  }

  async startCollector() {
    if (this.collectorProcess) {
      vscode.window.showInformationMessage("Agent Quality collector is already running.");
      return;
    }
    const folder = await pickWorkspaceFolder();
    if (!folder) return;

    const cfg = getConfig();
    const args = [
      "serve-collector",
      "--host",
      cfg.get("collectorHost") || "127.0.0.1",
      "--port",
      String(cfg.get("collectorPort") || 8765)
    ];
    const token = cfg.get("collectorToken");
    if (token) args.push("--token", token);

    const invocation = aqInvocation(folder);
    const displayArgs = token
      ? [...args.slice(0, -1), "[configured]"]
      : args;
    this.output.show(true);
    this.output.appendLine(`$ ${quoteArgs([...invocation.commandLine, ...displayArgs])}`);
    const child = cp.spawn(invocation.command, [...invocation.prefixArgs, ...args], {
      cwd: projectRootPath(folder),
      env: commandEnv(folder),
      shell: false
    });
    this.collectorProcess = child;
    this.statusItem.text = "$(radio-tower) Agent Quality";

    child.stdout.on("data", (chunk) => this.output.append(chunk.toString()));
    child.stderr.on("data", (chunk) => this.output.append(chunk.toString()));
    child.on("error", (err) => {
      if (this.collectorProcess === child) this.collectorProcess = undefined;
      this.statusItem.text = "$(pulse) Agent Quality";
      vscode.window.showErrorMessage(`Failed to start Agent Quality collector: ${err.message}`);
    });
    child.on("exit", (code, signal) => {
      if (this.collectorProcess === child) this.collectorProcess = undefined;
      this.statusItem.text = "$(pulse) Agent Quality";
      this.output.appendLine(`collector exited code=${code} signal=${signal || ""}`);
    });
  }

  stopCollector() {
    if (!this.collectorProcess) {
      vscode.window.showInformationMessage("Agent Quality collector is not running.");
      return;
    }
    this.collectorProcess.kill();
    this.collectorProcess = undefined;
    this.statusItem.text = "$(pulse) Agent Quality";
  }

  async reportSummary() {
    const folder = await pickWorkspaceFolder();
    if (!folder) return;
    await this.runAq(["report", "summary"], folder, {
      title: "Summary",
      reveal: true
    });
    this.refreshRuns();
  }

  runAq(args, folder, options = {}) {
    const invocation = aqInvocation(folder);
    const title = options.title || args.join(" ");
    const displayArgs = options.displayArgs || args;
    this.output.show(Boolean(options.reveal));
    this.output.appendLine("");
    this.output.appendLine(`[${title}]`);
    this.output.appendLine(`$ ${quoteArgs([...invocation.commandLine, ...displayArgs])}`);
    this.statusItem.text = "$(sync~spin) Agent Quality";

    return new Promise((resolve) => {
      const child = cp.spawn(invocation.command, [...invocation.prefixArgs, ...args], {
        cwd: commandWorkingDirectory(folder),
        env: commandEnv(folder, { projectHome: options.projectHome !== false }),
        shell: false
      });
      let stderr = "";
      let settled = false;
      child.stdout.on("data", (chunk) => this.output.append(chunk.toString()));
      child.stderr.on("data", (chunk) => {
        const text = chunk.toString();
        stderr += text;
        this.output.append(text);
      });
      child.on("error", (err) => {
        if (settled) return;
        settled = true;
        this.resetStatus();
        vscode.window.showErrorMessage(`Agent Quality command failed to start: ${err.message}`);
        resolve(undefined);
      });
      child.on("exit", (code) => {
        if (settled) return;
        settled = true;
        this.resetStatus();
        this.output.appendLine(`exit=${code}`);
        if (code !== 0) {
          const detail = stderr.trim().split(/\r?\n/).slice(-1)[0];
          vscode.window.showErrorMessage(
            detail || `Agent Quality command exited with code ${code}.`
          );
        }
        resolve(undefined);
      });
    });
  }

  resetStatus() {
    this.statusItem.text = this.collectorProcess
      ? "$(radio-tower) Agent Quality"
      : "$(pulse) Agent Quality";
  }
}

module.exports = { CommandController };
