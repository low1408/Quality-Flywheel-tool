(function () {
  "use strict";

  const vscode = acquireVsCodeApi();
  const state = {
    analyses: [],
    candidates: [],
    details: null,
    pending: new Map(),
    running: false,
    selectedAnalysisId: null,
    selectedRuns: new Set()
  };
  const elements = {};

  document.addEventListener("DOMContentLoaded", init);

  function init() {
    elements.candidates = document.getElementById("candidates");
    elements.analyses = document.getElementById("analyses");
    elements.details = document.getElementById("details");
    elements.progress = document.getElementById("progress");
    elements.analyzeButton = document.getElementById("analyzeButton");
    document.addEventListener("click", handleClick);
    document.addEventListener("change", handleChange);
    window.addEventListener("message", handleHostMessage);
    refresh();
  }

  function handleHostMessage(event) {
    const message = event.data || {};
    if (message.command === "analysisEvent") {
      const item = message.event || {};
      if (item.type === "analysis_started") {
        elements.progress.textContent = `Analyzing 0 of ${item.total}`;
      } else if (item.type === "analysis_progress") {
        elements.progress.textContent = `Analyzing ${item.completed} of ${item.total}: ${item.run_id}`;
      } else if (item.type === "analysis_complete") {
        elements.progress.textContent = `${item.status}: ${item.failure_count} failures in ${item.cluster_count} clusters`;
        state.selectedAnalysisId = item.analysis_id;
      }
      return;
    }
    if (message.command === "analysisFinished") {
      state.running = false;
      elements.analyzeButton.disabled = false;
      if (message.error) {
        elements.progress.textContent = `Failed: ${message.error}`;
      }
      loadAnalyses(true);
      return;
    }
    if (!message.requestId || !state.pending.has(message.requestId)) return;
    const pending = state.pending.get(message.requestId);
    state.pending.delete(message.requestId);
    if (message.error) pending.reject(new Error(message.error));
    else pending.resolve(message);
  }

  function handleClick(event) {
    const target = event.target.closest("[data-action]");
    if (!target) return;
    const action = target.dataset.action;
    if (action === "refresh") refresh();
    else if (action === "selectDefaults") selectDefaults();
    else if (action === "analyze") startAnalysis();
    else if (action === "selectAnalysis") selectAnalysis(target.dataset.analysisId);
    else if (action === "openRun") request("openRun", { run_id: target.dataset.runId }).catch(showError);
  }

  function handleChange(event) {
    if (!event.target.matches("input[data-run-id]")) return;
    if (event.target.checked) state.selectedRuns.add(event.target.dataset.runId);
    else state.selectedRuns.delete(event.target.dataset.runId);
  }

  async function refresh() {
    await Promise.all([loadCandidates(), loadAnalyses(false)]);
  }

  async function loadCandidates() {
    try {
      const result = await request("loadCandidates");
      state.candidates = result.runs || [];
      if (!state.selectedRuns.size) {
        state.candidates.filter((run) => run.default_selected).forEach((run) => state.selectedRuns.add(run.id));
      }
      renderCandidates();
    } catch (error) { showError(error); }
  }

  async function loadAnalyses(selectCurrent) {
    try {
      const result = await request("loadAnalyses");
      state.analyses = result.analyses || [];
      if (!state.selectedAnalysisId && state.analyses.length) state.selectedAnalysisId = state.analyses[0].id;
      renderAnalyses();
      if ((selectCurrent || state.selectedAnalysisId) && state.selectedAnalysisId) await selectAnalysis(state.selectedAnalysisId);
    } catch (error) { showError(error); }
  }

  function selectDefaults() {
    state.selectedRuns = new Set(state.candidates.filter((run) => run.default_selected).map((run) => run.id));
    renderCandidates();
  }

  async function startAnalysis() {
    if (state.running) return;
    state.running = true;
    elements.analyzeButton.disabled = true;
    elements.progress.textContent = "Waiting for confirmation";
    try {
      const result = await request("startAnalysis", { run_ids: Array.from(state.selectedRuns) });
      if (!result.started) {
        state.running = false;
        elements.analyzeButton.disabled = false;
        elements.progress.textContent = "Idle";
      }
    } catch (error) {
      state.running = false;
      elements.analyzeButton.disabled = false;
      showError(error);
    }
  }

  async function selectAnalysis(analysisId) {
    if (!analysisId) return;
    state.selectedAnalysisId = analysisId;
    renderAnalyses();
    try {
      state.details = await request("loadAnalysisDetails", { analysis_id: analysisId });
      renderDetails();
    } catch (error) { showError(error); }
  }

  function renderCandidates() {
    if (!state.candidates.length) {
      elements.candidates.innerHTML = '<div class="empty">No completed runs with captured events.</div>';
      return;
    }
    elements.candidates.innerHTML = state.candidates.map((run) => `
      <label class="candidate">
        <input type="checkbox" data-run-id="${attr(run.id)}"${state.selectedRuns.has(run.id) ? " checked" : ""}>
        <span>
          <span class="candidate-title">${html(summary(run.prompt || run.id, 90))}</span>
          <span class="meta">${html([run.agent_adapter, run.model, run.verifier_status, run.human_status].filter(Boolean).join(" · "))}</span>
        </span>
      </label>
    `).join("");
  }

  function renderAnalyses() {
    if (!state.analyses.length) {
      elements.analyses.innerHTML = '<div class="empty">No analyses yet.</div>';
      return;
    }
    elements.analyses.innerHTML = state.analyses.map((analysis) => `
      <button class="analysis-card${analysis.id === state.selectedAnalysisId ? " active" : ""}" data-action="selectAnalysis" data-analysis-id="${attr(analysis.id)}">
        <strong>${html(analysis.status)}</strong>
        <span class="meta">${html(formatDate(analysis.created_at))}</span>
        <span class="meta">${analysis.failure_count || 0} failures · ${analysis.cluster_count || 0} clusters</span>
      </button>
    `).join("");
  }

  function renderDetails() {
    const details = state.details;
    if (!details || !details.analysis) return;
    const analysis = details.analysis;
    elements.details.innerHTML = `
      <div class="details-head">
        <div><h2>${html(analysis.id)}</h2><p>${html(formatDate(analysis.created_at))}</p></div>
        <div class="chips"><span class="chip">${html(analysis.status)}</span><span class="chip">${analysis.selected_run_count || 0} runs</span></div>
      </div>
      ${analysis.error_message ? `<p class="error">${html(analysis.error_message)}</p>` : ""}
      <h2>Clusters</h2>
      ${details.clusters.length ? details.clusters.map(renderCluster).join("") : '<div class="empty">No cluster met the configured minimum size.</div>'}
      <h2 style="margin-top:16px">Run results</h2>
      <div class="stack">${details.inputs.map(renderInput).join("") || '<div class="empty">No recorded inputs.</div>'}</div>
    `;
  }

  function renderCluster(cluster) {
    const runs = cluster.affected_runs || [];
    const diagnoses = (state.details.failures || []).filter((failure) => failure.cluster_id === cluster.id);
    return `<article class="cluster">
      <div class="cluster-head"><h3>${html(cluster.title)}</h3><div class="chips"><span class="chip">${html(cluster.primary_category || "unknown")}</span><span class="chip">${html(cluster.severity || "unknown")}</span><span class="chip">${cluster.occurrence_count || 0}</span></div></div>
      <p>${html(cluster.description || "")}</p>
      ${cluster.proposed_intervention ? `<p><strong>Intervention:</strong> ${html(cluster.proposed_intervention)}</p>` : ""}
      ${diagnoses.length ? `<div class="stack">${diagnoses.map((failure) => `<div><strong>${html(failure.subcategory || failure.category || "Failure")}</strong><span class="meta">${html(failure.description)}</span></div>`).join("")}</div>` : ""}
      <div class="run-links">${runs.map((runId) => `<button class="secondary run-link" data-action="openRun" data-run-id="${attr(runId)}">${html(runId)}</button>`).join("")}</div>
    </article>`;
  }

  function renderInput(input) {
    return `<div class="input-row">
      <strong>${html(summary(input.prompt || input.run_id, 100))}</strong>
      <span class="meta">${html(input.status)} · ${input.failure_count || 0} failures</span>
      ${input.error_message ? `<span class="error">${html(input.error_type || "error")}: ${html(input.error_message)}</span>` : ""}
      <div class="run-links"><button class="secondary run-link" data-action="openRun" data-run-id="${attr(input.run_id)}">Open run</button></div>
    </div>`;
  }

  function request(command, payload) {
    const requestId = `fw_${Date.now()}_${Math.random().toString(16).slice(2)}`;
    return new Promise((resolve, reject) => {
      state.pending.set(requestId, { resolve, reject });
      vscode.postMessage({ command, requestId, ...(payload || {}) });
      window.setTimeout(() => {
        if (state.pending.has(requestId)) {
          state.pending.delete(requestId);
          reject(new Error(`${command} timed out`));
        }
      }, 30000);
    });
  }

  function showError(error) {
    elements.progress.textContent = `Error: ${error.message || String(error)}`;
  }
  function summary(value, length) { const text = String(value || "").replace(/\s+/g, " ").trim(); return text.length > length ? `${text.slice(0, length - 1)}…` : text; }
  function formatDate(value) { const date = new Date(value); return Number.isNaN(date.getTime()) ? String(value || "") : date.toLocaleString(); }
  function html(value) { return String(value == null ? "" : value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char])); }
  function attr(value) { return html(value); }
}());
