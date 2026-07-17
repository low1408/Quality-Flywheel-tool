"use strict";

const cp = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const vscode = require("vscode");

const AQ_COMMAND_MAX_BUFFER = 10 * 1024 * 1024;
const UI_API_MAX_BUFFER = 10 * 1024 * 1024;
const GIT_REPOSITORY_ENV = [
  "GIT_DIR",
  "GIT_WORK_TREE",
  "GIT_COMMON_DIR",
  "GIT_OBJECT_DIRECTORY",
  "GIT_INDEX_FILE",
  "GIT_NAMESPACE",
  "GIT_CEILING_DIRECTORIES"
];

function execAq(args, folder) {
  const invocation = aqInvocation(folder);
  return new Promise((resolve, reject) => {
    cp.execFile(invocation.command, [...invocation.prefixArgs, ...args], {
      cwd: projectRootPath(folder),
      env: commandEnv(folder),
      timeout: 15000,
      windowsHide: true,
      maxBuffer: AQ_COMMAND_MAX_BUFFER
    }, (err, stdout, stderr) => {
      if (err) {
        reject(new Error(stderr.trim() || err.message));
        return;
      }
      resolve({ stdout, stderr });
    });
  });
}

function runUiApi(folder, action, payload = {}) {
  const invocation = aqInvocation(folder);
  return new Promise((resolve, reject) => {
    const child = cp.execFile(
      invocation.command,
      [...invocation.prefixArgs, "ui-api", action],
      {
        cwd: projectRootPath(folder),
        env: commandEnv(folder),
        timeout: 15000,
        windowsHide: true,
        maxBuffer: UI_API_MAX_BUFFER
      },
      (err, stdout, stderr) => {
        if (err) {
          reject(new Error((stderr || "").trim() || err.message));
          return;
        }
        try {
          resolve(JSON.parse(stdout || "null"));
        } catch (parseErr) {
          reject(new Error(`Agent Quality UI API returned invalid JSON: ${parseErr.message}`));
        }
      }
    );
    child.stdin.on("error", (err) => reject(err));
    child.stdin.end(`${JSON.stringify(payload)}\n`, "utf8");
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

function commandEnv(folder, options = {}) {
  const env = { ...process.env };
  if (options.projectHome !== false) {
    env.AGENT_QUALITY_HOME = agentQualityHome(folder);
  }
  const sourceRoot = cliSourceRoot(folder);
  if (sourceRoot) {
    const srcPath = path.join(sourceRoot, "src");
    env.PYTHONPATH = env.PYTHONPATH
      ? `${srcPath}${path.delimiter}${env.PYTHONPATH}`
      : srcPath;
  }
  return env;
}

function aqInvocation(folder) {
  const configured = getConfig().get("aqCommand") || "aq";
  const commandLine = splitCommandLine(configured);
  if (commandLine.length === 0) {
    return { command: "aq", prefixArgs: [], commandLine: ["aq"] };
  }
  return {
    command: commandLine[0],
    prefixArgs: commandLine.slice(1),
    commandLine
  };
}

function cliSourceRoot(folder) {
  const configured = getConfig().get("cliSourceRoot");
  const repo = commandWorkingDirectory(folder);
  if (configured) {
    return path.isAbsolute(configured) ? configured : path.join(repo, configured);
  }
  return cliSourceRootCandidates(repo).find(hasCliSource);
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
    // Explicit candidates still support unreadable or transient workspace roots.
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
    } else if (char === "\\") {
      escaped = true;
    } else if (quote) {
      if (char === quote) {
        quote = "";
      } else {
        current += char;
      }
    } else if (char === "'" || char === "\"") {
      quote = char;
    } else if (/\s/.test(char)) {
      if (current) {
        parts.push(current);
        current = "";
      }
    } else {
      current += char;
    }
  }
  if (escaped) {
    current += "\\";
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
  const workspacePath = path.resolve(folder.uri.fsPath);
  let candidate = workspacePath;
  try {
    if (fs.statSync(candidate).isFile()) {
      candidate = path.dirname(candidate);
    }
  } catch {
    return workspacePath;
  }

  const env = { ...process.env };
  for (const name of GIT_REPOSITORY_ENV) delete env[name];
  const result = cp.spawnSync(
    "git",
    ["-C", candidate, "rev-parse", "--show-toplevel"],
    {
      encoding: "utf8",
      env,
      timeout: 3000,
      windowsHide: true
    }
  );
  if (result.status === 0 && result.stdout.trim()) {
    return path.resolve(result.stdout.trim());
  }
  return workspacePath;
}

function commandWorkingDirectory(folder) {
  if (folder) {
    return projectRootPath(folder);
  }
  const current = process.cwd();
  try {
    if (fs.statSync(current).isDirectory()) {
      return current;
    }
  } catch {
    // Fall back to a stable existing directory.
  }
  return os.homedir();
}

function optionalWorkspaceFolder() {
  return (vscode.workspace.workspaceFolders || [])[0];
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
  })), { title: "Select workspace folder" });
  return picked && picked.folder;
}

function firstWorkspaceFolder() {
  return (vscode.workspace.workspaceFolders || [])[0];
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
  agentQualityHome,
  aqInvocation,
  commandEnv,
  commandWorkingDirectory,
  configuredVerifyPath,
  execAq,
  firstWorkspaceFolder,
  getConfig,
  optionalWorkspaceFolder,
  pickWorkspaceFolder,
  projectRootPath,
  quoteArgs,
  runUiApi,
  splitCommandLine
};
