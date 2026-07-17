"use strict";

const crypto = require("crypto");
const fs = require("fs");
const vscode = require("vscode");

function mediaHtml(context, panel, name) {
  const mediaRoot = vscode.Uri.joinPath(context.extensionUri, "media");
  const htmlPath = vscode.Uri.joinPath(mediaRoot, `${name}.html`);
  const cssUri = panel.webview.asWebviewUri(
    vscode.Uri.joinPath(mediaRoot, `${name}.css`)
  );
  const jsUri = panel.webview.asWebviewUri(
    vscode.Uri.joinPath(mediaRoot, `${name}.js`)
  );
  const nonce = crypto.randomBytes(18).toString("base64");
  const policy = [
    "default-src 'none'",
    `img-src ${panel.webview.cspSource} data:`,
    `style-src ${panel.webview.cspSource}`,
    `script-src 'nonce-${nonce}'`
  ].join("; ");

  let html = fs.readFileSync(htmlPath.fsPath, "utf8");
  html = html.replace(`./${name}.css`, String(cssUri));
  html = html.replace(`./${name}.js`, String(jsUri));
  html = html.replace(
    "</head>",
    `  <meta http-equiv="Content-Security-Policy" content="${policy}">\n  </head>`
  );
  html = html.replace("<script ", `<script nonce="${nonce}" `);
  return html;
}

async function openTextFile(filePath, line) {
  const document = await vscode.workspace.openTextDocument(vscode.Uri.file(filePath));
  const editor = await vscode.window.showTextDocument(document, { preview: false });
  const lineNumber = Number(line);
  if (!Number.isFinite(lineNumber) || lineNumber <= 0) return;
  const position = new vscode.Position(lineNumber - 1, 0);
  editor.selection = new vscode.Selection(position, position);
  editor.revealRange(
    new vscode.Range(position, position),
    vscode.TextEditorRevealType.InCenter
  );
}

module.exports = { mediaHtml, openTextFile };
