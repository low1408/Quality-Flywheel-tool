(function () {
  "use strict";

  const vscode = typeof acquireVsCodeApi === "function" ? acquireVsCodeApi() : null;
  const initialRunId = window.__AGENT_QUALITY_INITIAL_RUN_ID__ || null;
  const tabs = [
    ["overview", "Overview"],
    ["verifiers", "Verifiers"],
    ["artifacts", "Artifacts"],
    ["timeline", "Timeline"],
    ["review", "Review"]
  ];
  const outcomes = [
    ["not_reviewed", "Not reviewed"],
    ["accepted_cleanly", "Accepted cleanly"],
    ["accepted_with_minor_edits", "Accepted with minor edits"],
    ["accepted_with_major_edits", "Accepted with major edits"],
    ["partial", "Partial"],
    ["rejected", "Rejected"]
  ];
  const categories = [
    ["", "None"],
    ["specification", "Specification"],
    ["context", "Context"],
    ["fault_localization", "Fault localization"],
    ["planning", "Planning"],
    ["implementation", "Implementation"],
    ["tool_use", "Tool use"],
    ["verification", "Verification"],
    ["environment", "Environment"],
    ["scope_control", "Scope control"],
    ["reporting", "Reporting"],
    ["unknown", "Unknown"]
  ];
  const severities = [
    ["", "None"],
    ["low", "Low"],
    ["medium", "Medium"],
    ["high", "High"],
    ["critical", "Critical"]
  ];

  const state = {
    activeTab: "overview",
    details: null,
    pending: new Map(),
    query: "",
    runs: [],
    saveTimer: 0,
    selectedEventId: null,
    selectedRunId: initialRunId
  };
  const elements = {};

  document.addEventListener("DOMContentLoaded", init);

  function init() {
    elements.hostLabel = document.getElementById("hostLabel");
    elements.syncState = document.getElementById("syncState");
    elements.runCount = document.getElementById("runCount");
    elements.runList = document.getElementById("runList");
    elements.runSearch = document.getElementById("runSearch");
    elements.statusFilter = document.getElementById("statusFilter");
    elements.detailPane = document.getElementById("detailPane");
    elements.modalBackdrop = document.getElementById("modalBackdrop");
    elements.modalTitle = document.getElementById("modalTitle");
    elements.modalContent = document.getElementById("modalContent");

    elements.hostLabel.textContent = vscode ? "VS Code" : "Browser";
    document.addEventListener("click", handleClick);
    document.addEventListener("input", handleInput);
    document.addEventListener("change", handleChange);
    window.addEventListener("message", handleHostMessage);
    loadRuns();
  }

  function handleHostMessage(event) {
    const message = event.data || {};
    if (message.command === "selectRun" && message.run_id) {
      state.selectedRunId = message.run_id;
      state.activeTab = message.tab || state.activeTab;
      state.selectedEventId = null;
      renderRuns();
      loadRunDetails(message.run_id);
      return;
    }
    if (!message.requestId || !state.pending.has(message.requestId)) {
      return;
    }
    const pending = state.pending.get(message.requestId);
    state.pending.delete(message.requestId);
    if (message.error) {
      pending.reject(new Error(message.error));
      return;
    }
    pending.resolve(message);
  }

  function handleClick(event) {
    const target = event.target.closest("[data-action]");
    if (!target) {
      return;
    }
    const action = target.dataset.action;
    if (action === "refresh") {
      loadRuns();
    } else if (action === "selectRun") {
      selectRun(target.dataset.runId);
    } else if (action === "setTab") {
      state.activeTab = target.dataset.tab || "overview";
      renderDetail();
    } else if (action === "openPath") {
      openPath(target.dataset.path, target.dataset.line);
    } else if (action === "openDiff") {
      openDiff();
    } else if (action === "selectEvent") {
      state.selectedEventId = target.dataset.eventId || null;
      renderDetail();
    } else if (action === "saveReview") {
      saveReviewNow();
    } else if (action === "closeModal") {
      closeModal();
    }
  }

  function handleInput(event) {
    if (event.target === elements.runSearch) {
      state.query = event.target.value.trim().toLowerCase();
      renderRuns();
      return;
    }
    if (event.target.name === "confidence") {
      const output = document.getElementById("confidenceValue");
      if (output) {
        output.textContent = Number(event.target.value).toFixed(2);
      }
    }
    if (event.target.closest("#reviewForm")) {
      scheduleReviewSave();
    }
  }

  function handleChange(event) {
    if (event.target === elements.statusFilter) {
      renderRuns();
      return;
    }
    if (event.target.closest("#reviewForm")) {
      scheduleReviewSave();
    }
  }

  async function loadRuns() {
    setSync("Loading");
    try {
      const runs = await request("loadRuns");
      state.runs = Array.isArray(runs) ? runs : runs.runs || [];
      if (!state.selectedRunId && state.runs.length) {
        state.selectedRunId = state.runs[0].id;
      }
      if (state.selectedRunId && !state.runs.some((run) => run.id === state.selectedRunId)) {
        state.selectedRunId = state.runs.length ? state.runs[0].id : null;
      }
      renderRuns();
      if (state.selectedRunId) {
        await loadRunDetails(state.selectedRunId);
      } else {
        state.details = null;
        renderDetail();
      }
      setSync("Idle");
    } catch (error) {
      setSync("Error");
      renderError(error);
    }
  }

  async function selectRun(runId) {
    if (!runId) {
      return;
    }
    state.selectedRunId = runId;
    state.activeTab = "overview";
    state.selectedEventId = null;
    renderRuns();
    await loadRunDetails(runId);
  }

  async function loadRunDetails(runId) {
    setSync("Loading");
    state.details = null;
    renderDetail(true);
    try {
      state.details = await request("loadRunDetails", { run_id: runId });
      renderDetail();
      setSync("Idle");
    } catch (error) {
      setSync("Error");
      renderError(error);
    }
  }

  function renderRuns() {
    const filtered = filteredRuns();
    elements.runCount.textContent = `${filtered.length} of ${state.runs.length}`;
    if (!filtered.length) {
      elements.runList.innerHTML = '<div class="empty-copy">No runs found.</div>';
      return;
    }
    elements.runList.innerHTML = filtered.map((run) => {
      const active = run.id === state.selectedRunId ? " is-active" : "";
      const prompt = summaryText(run.prompt, 96, "No prompt captured");
      const meta = [
        run.started_at ? formatDate(run.started_at) : "",
        run.model || ""
      ].filter(Boolean).join(" - ");
      return `
        <button type="button" class="run-card${active}" data-action="selectRun" data-run-id="${escapeAttr(run.id)}">
          <span class="run-card-main">
            <span class="run-title">${escapeHtml(prompt)}</span>
            ${statusChip(run.verifier_status || "unverified")}
          </span>
          ${meta ? `<span class="run-meta">${escapeHtml(meta)}</span>` : ""}
          <span class="status-row">
            ${statusChip(run.agent_status || "agent_unknown")}
            ${statusChip(run.human_status || "not_reviewed")}
          </span>
        </button>
      `;
    }).join("");
  }

  function filteredRuns() {
    const filter = elements.statusFilter.value;
    return state.runs.filter((run) => {
      const haystack = [
        run.id,
        run.prompt,
        run.agent_adapter,
        run.model,
        run.repository_path,
        run.agent_status,
        run.verifier_status,
        run.human_status
      ].filter(Boolean).join(" ").toLowerCase();
      if (state.query && !haystack.includes(state.query)) {
        return false;
      }
      if (filter === "passed") {
        return run.verifier_status === "passed";
      }
      if (filter === "failed") {
        return run.verifier_status === "failed" || run.agent_status === "failed";
      }
      if (filter === "reviewed") {
        return run.human_status && !["not_reviewed", "review_skipped"].includes(run.human_status);
      }
      if (filter === "unreviewed") {
        return !run.human_status || run.human_status === "not_reviewed";
      }
      return true;
    });
  }

  function renderDetail(loading) {
    if (loading) {
      elements.detailPane.innerHTML = '<div class="empty-state"><h2>Loading run</h2><p>...</p></div>';
      return;
    }
    const details = state.details;
    if (!details || !details.run) {
      elements.detailPane.innerHTML = '<div class="empty-state"><h2>No run selected</h2><p>No active selection.</p></div>';
      return;
    }
    const run = details.run;
    const selectedTabId = `run-tab-${state.activeTab}`;
    elements.detailPane.innerHTML = `
      <div class="detail-layout">
        <div class="detail-head">
          <div class="detail-title">
            <h2>${escapeHtml(summaryText(run.prompt, 104, "Run details"))}</h2>
            <p>${escapeHtml(run.repository_path || "n/a")}</p>
            <div class="status-row">
              ${statusChip(run.agent_status || "agent_unknown")}
              ${statusChip(run.verifier_status || "unverified")}
              ${statusChip(run.human_status || "not_reviewed")}
              ${statusChip(run.lifecycle_status || "lifecycle_unknown")}
            </div>
          </div>
          <div class="detail-actions">
            <button type="button" class="button ghost" data-action="openDiff">Open Diff</button>
            <button type="button" class="button primary" data-action="setTab" data-tab="review">Review</button>
          </div>
        </div>
        <div class="tabs" role="tablist" aria-label="Run views">
          ${tabs.map(([id, label]) => `
            <button
              type="button"
              id="run-tab-${id}"
              class="tab-button${state.activeTab === id ? " is-active" : ""}"
              role="tab"
              aria-selected="${state.activeTab === id}"
              aria-controls="run-tab-panel"
              data-action="setTab"
              data-tab="${id}"
            >${label}</button>
          `).join("")}
        </div>
        <div id="run-tab-panel" class="content-panel" role="tabpanel" aria-labelledby="${selectedTabId}">
          ${renderActiveTab(details)}
        </div>
      </div>
    `;
  }

  function renderActiveTab(details) {
    if (state.activeTab === "verifiers") {
      return renderVerifiers(details.verifier_results || []);
    }
    if (state.activeTab === "artifacts") {
      return renderArtifacts(details.artifacts || []);
    }
    if (state.activeTab === "timeline") {
      return renderTimeline(details.events || []);
    }
    if (state.activeTab === "review") {
      return renderReview(details);
    }
    return renderOverview(details);
  }

  function renderOverview(details) {
    const run = details.run;
    const outputs = details.agent_outputs || [];
    const latestOutput = outputs.length ? outputs[outputs.length - 1] : null;
    const reasoningTrace = details.reasoning_trace || [];
    const toolCalls = details.tool_calls || [];
    return `
      <div class="overview-stack">
        <div class="overview-primary">
          <section class="reading-section" aria-labelledby="prompt-heading">
            <h3 id="prompt-heading" class="section-title">Prompt</h3>
            <pre class="prompt-block reading-content">${escapeHtml(run.prompt || "No prompt captured.")}</pre>
          </section>
          <section class="reading-section" aria-labelledby="output-heading">
            <h3 id="output-heading" class="section-title">Agent output</h3>
            ${latestOutput ? outputBlock(latestOutput) : '<div class="empty-copy reading-empty">No agent output captured yet.</div>'}
          </section>
          <section class="reading-section" aria-labelledby="reasoning-heading">
            <h3 id="reasoning-heading" class="section-title">Reasoning trace</h3>
            <p class="section-note">Shows emitted commentary and reasoning summaries. Private chain-of-thought remains encrypted by Codex and is not available to the collector.</p>
            ${reasoningTraceBlock(reasoningTrace)}
          </section>
          <section class="reading-section" aria-labelledby="tools-heading">
            <h3 id="tools-heading" class="section-title">Tool calls</h3>
            ${toolCallsBlock(toolCalls)}
          </section>
        </div>
        <details class="overview-secondary">
          <summary>Run details</summary>
          <div class="overview-grid">
            ${kv("Started", formatDate(run.started_at))}
            ${kv("Completed", formatDate(run.completed_at))}
            ${kv("Duration", formatDuration(run.duration_ms))}
            ${kv("Model", value(run.model))}
            ${kv("Agent", value(run.agent_adapter))}
            ${kv("Turn", value(run.turn_number))}
            ${kv("Base commit", value(run.base_commit))}
            ${kv("Resulting commit", value(run.resulting_commit))}
            ${kv("Session", value(run.session_id))}
            ${kv("Verifier version", value(run.verifier_version))}
          </div>
        </details>
      </div>
    `;
  }

  function renderVerifiers(results) {
    if (!results.length) {
      return '<div class="empty-copy">No verifier results.</div>';
    }
    return `
      <div class="stack">
        ${results.map((result) => `
          <div class="record">
            <div class="record-head">
              <div>
                <div class="record-title">${escapeHtml(result.verifier_category || "verifier")} / ${escapeHtml(result.verifier_name || "unnamed")}</div>
                <div class="record-subtitle">${escapeHtml(result.command || "n/a")}</div>
              </div>
              ${statusChip(result.passed ? "passed" : "failed")}
            </div>
            <div class="status-row">
              ${statusChip(`exit_${value(result.exit_code)}`)}
              ${statusChip(formatDuration(result.duration_ms))}
              ${statusChip(formatDate(result.started_at))}
            </div>
            <div class="record-actions">
              ${pathButton("Open stdout", result.stdout_path)}
              ${pathButton("Open stderr", result.stderr_path)}
            </div>
          </div>
        `).join("")}
      </div>
    `;
  }

  function renderArtifacts(artifacts) {
    if (!artifacts.length) {
      return '<div class="empty-copy">No artifacts.</div>';
    }
    return `
      <div class="stack">
        ${artifacts.map((artifact) => `
          <div class="record">
            <div class="record-head">
              <div>
                <div class="record-title">${escapeHtml(artifact.artifact_type || "artifact")}</div>
                <div class="path-text">${escapeHtml(artifact.path || "n/a")}</div>
              </div>
              <div class="record-actions">
                ${pathButton(vscode ? "Open File" : "Preview", artifact.path, artifact.line)}
              </div>
            </div>
            <div class="status-row">
              ${statusChip(formatBytes(artifact.size_bytes))}
              ${statusChip(artifact.sha256 ? compact(artifact.sha256, 18) : "no_sha")}
            </div>
          </div>
        `).join("")}
      </div>
    `;
  }

  function renderTimeline(events) {
    if (!events.length) {
      return '<div class="empty-copy">No events.</div>';
    }
    if (!state.selectedEventId) {
      state.selectedEventId = String(events[0].id || events[0].sequence_number || 0);
    }
    const selected = events.find((event) => eventKey(event) === state.selectedEventId) || events[0];
    return `
      <div class="event-grid">
        <div class="event-list">
          ${events.map((event) => `
            <button type="button" class="event-row${eventKey(event) === eventKey(selected) ? " is-active" : ""}" data-action="selectEvent" data-event-id="${escapeAttr(eventKey(event))}">
              <span class="event-seq">#${escapeHtml(value(event.sequence_number))}</span>
              <span class="event-main">
                <strong>${escapeHtml(event.event_type || event.source_event_type || "event")}</strong>
                <span class="event-meta">${escapeHtml(event.command || event.path || event.item_type || event.status || "n/a")}</span>
              </span>
              ${statusChip(event.status || event.tool_category || "event")}
            </button>
          `).join("")}
        </div>
        <div class="event-detail">
          ${eventSummary(selected)}
          <pre class="json-block">${escapeHtml(JSON.stringify(selectedEventPayload(selected), null, 2))}</pre>
        </div>
      </div>
    `;
  }

  function renderReview(details) {
    const run = details.run;
    const review = (details.human_reviews || [])[0] || {};
    const confidence = Number.isFinite(Number(review.confidence)) ? Number(review.confidence) : 0.75;
    return `
      <form id="reviewForm" class="review-form">
        <div class="form-grid">
          ${selectField("Outcome", "outcome", outcomes, review.outcome || run.human_status || "not_reviewed")}
          ${selectField("Primary category", "primary_category", categories, review.primary_failure_category || "")}
          ${selectField("Severity", "severity", severities, review.severity || "")}
          <label class="form-field">
            <span>Critical sequence</span>
            <input type="number" name="critical_sequence" min="0" step="1" value="${escapeAttr(review.critical_event_sequence ?? "")}">
          </label>
          <label class="form-field full">
            <span>Confidence</span>
            <span class="range-row">
              <input type="range" name="confidence" min="0" max="1" step="0.05" value="${escapeAttr(confidence)}">
              <output id="confidenceValue">${confidence.toFixed(2)}</output>
            </span>
          </label>
          <label class="form-field full">
            <span>Notes</span>
            <textarea name="notes">${escapeHtml(review.notes || "")}</textarea>
          </label>
        </div>
        <div class="detail-actions">
          <span id="reviewStatus" class="review-status">Idle</span>
          <button type="button" class="button primary" data-action="saveReview">Save Review</button>
        </div>
      </form>
    `;
  }

  function scheduleReviewSave() {
    if (state.activeTab !== "review" || !state.details) {
      return;
    }
    window.clearTimeout(state.saveTimer);
    setReviewStatus("Saving");
    state.saveTimer = window.setTimeout(saveReviewNow, 650);
  }

  async function saveReviewNow() {
    const form = document.getElementById("reviewForm");
    if (!form || !state.details || !state.details.run) {
      return;
    }
    window.clearTimeout(state.saveTimer);
    setReviewStatus("Saving");
    try {
      const formData = new FormData(form);
      const payload = {
        run_id: state.details.run.id,
        outcome: String(formData.get("outcome") || "not_reviewed"),
        primary_category: emptyToNull(formData.get("primary_category")),
        severity: emptyToNull(formData.get("severity")),
        notes: String(formData.get("notes") || ""),
        confidence: Number(formData.get("confidence")),
        critical_sequence: emptyToNull(formData.get("critical_sequence"))
      };
      const response = await request("saveReview", payload);
      const review = response.review || response;
      state.details.human_reviews = [review];
      state.details.run.human_status = review.outcome;
      const run = state.runs.find((item) => item.id === state.details.run.id);
      if (run) {
        run.human_status = review.outcome;
      }
      renderRuns();
      setReviewStatus("Saved");
      setSync("Idle");
    } catch (error) {
      setReviewStatus("Save failed");
      setSync("Error");
      showModal("Save failed", error.message || String(error));
    }
  }

  async function openPath(filePath, line) {
    if (!filePath) {
      return;
    }
    if (vscode) {
      await request("openFile", { path: filePath, line });
      return;
    }
    await previewPath(filePath);
  }

  async function previewPath(filePath) {
    setSync("Loading");
    try {
      const result = await request("readLog", { path: filePath });
      const suffix = result.truncated ? "\n\n[truncated]" : "";
      showModal(result.path || filePath, (result.content || "") + suffix);
      setSync("Idle");
    } catch (error) {
      setSync("Error");
      showModal("Preview failed", error.message || String(error));
    }
  }

  async function openDiff() {
    const runId = state.details && state.details.run && state.details.run.id;
    if (!runId) {
      return;
    }
    if (vscode) {
      await request("openDiff", { run_id: runId });
      return;
    }
    const artifacts = state.details.artifacts || [];
    const patch = artifacts.find((artifact) => ["final_patch", "diff", "patch"].includes(artifact.artifact_type));
    if (patch && patch.path) {
      await previewPath(patch.path);
      return;
    }
    showModal("Diff", `aq diff ${runId}`);
  }

  async function request(command, payload) {
    if (vscode) {
      return requestVsCode(command, payload || {});
    }
    if (command === "loadRuns") {
      const response = await fetchJson("/v1/ui/api/runs");
      return response;
    }
    if (command === "loadRunDetails") {
      return fetchJson(`/v1/ui/api/run/${encodeURIComponent(payload.run_id)}`);
    }
    if (command === "saveReview") {
      return fetchJson("/v1/ui/api/review", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
    }
    if (command === "readLog") {
      return fetchJson(`/v1/ui/api/log?path=${encodeURIComponent(payload.path)}`);
    }
    throw new Error(`Unsupported command: ${command}`);
  }

  function requestVsCode(command, payload) {
    const requestId = `req_${Date.now()}_${Math.random().toString(16).slice(2)}`;
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

  async function fetchJson(url, options) {
    const response = await fetch(url, options);
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      throw new Error((payload && payload.error) || `${response.status} ${response.statusText}`);
    }
    return payload;
  }

  function renderError(error) {
    elements.detailPane.innerHTML = `
      <div class="empty-state">
        <h2>Error</h2>
        <p>${escapeHtml(error.message || String(error))}</p>
      </div>
    `;
  }

  function pathButton(label, filePath, line) {
    if (!filePath) {
      return "";
    }
    const lineAttr = line ? ` data-line="${escapeAttr(line)}"` : "";
    return `<button type="button" class="button ghost" data-action="openPath" data-path="${escapeAttr(filePath)}"${lineAttr}>${escapeHtml(label)}</button>`;
  }

  function outputBlock(output) {
    const occurredAt = output.occurred_at ? formatDate(output.occurred_at) : "";
    return `
      <div class="output-block">
        ${occurredAt ? `<div class="output-meta">Latest response - ${escapeHtml(occurredAt)}</div>` : ""}
        <pre class="prompt-block reading-content">${escapeHtml(output.text || "")}</pre>
        ${fileLinksBlock(output.file_links || [])}
      </div>
    `;
  }

  function reasoningTraceBlock(trace) {
    if (!trace.length) {
      return '<div class="empty-copy trace-empty">No emitted reasoning summaries or commentary captured.</div>';
    }
    return `
      <div class="trace-list">
        ${trace.map((entry) => `
          <article class="trace-entry reasoning-entry">
            <div class="trace-head">
              <span class="trace-kind">${escapeHtml(humanize(entry.kind || "reasoning"))}</span>
              ${entry.occurred_at ? `<time>${escapeHtml(formatDate(entry.occurred_at))}</time>` : ""}
            </div>
            <pre class="trace-content">${escapeHtml(entry.text || "")}</pre>
          </article>
        `).join("")}
      </div>
    `;
  }

  function toolCallsBlock(calls) {
    if (!calls.length) {
      return '<div class="empty-copy trace-empty">No tool calls captured.</div>';
    }
    return `
      <div class="trace-list">
        ${calls.map((call) => `
          <details class="trace-entry tool-entry">
            <summary>
              <span class="tool-name">${escapeHtml(call.tool_name || "tool")}</span>
              <span class="status-row">
                ${call.tool_category ? statusChip(call.tool_category) : ""}
                ${statusChip(call.status || "observed")}
              </span>
            </summary>
            <div class="tool-detail">
              ${call.occurred_at ? `<div class="output-meta">${escapeHtml(formatDate(call.occurred_at))}</div>` : ""}
              ${traceValue("Input", call.input)}
              ${traceValue("Output", call.output)}
            </div>
          </details>
        `).join("")}
      </div>
    `;
  }

  function traceValue(label, data) {
    if (data === null || data === undefined || data === "") {
      return "";
    }
    const text = typeof data === "string" ? data : JSON.stringify(data, null, 2);
    return `
      <div>
        <div class="trace-label">${escapeHtml(label)}</div>
        <pre class="trace-content">${escapeHtml(compact(text, 12000))}</pre>
      </div>
    `;
  }

  function eventSummary(event) {
    const payload = event.normalized_payload_json || {};
    const assistantOutput = payload.assistant_output || event.assistant_output;
    const toolOutput = payload.tool_output || event.tool_output;
    const links = payload.file_links || event.file_links || [];
    const artifacts = payload.artifacts || [];
    const parts = [];
    if (assistantOutput) {
      parts.push(`
        <div>
          <h3 class="section-title">Assistant Output</h3>
          <pre class="prompt-block">${escapeHtml(assistantOutput)}</pre>
        </div>
      `);
    }
    if (toolOutput) {
      parts.push(`
        <div>
          <h3 class="section-title">Tool Output</h3>
          <pre class="prompt-block">${escapeHtml(compact(toolOutput, 6000))}</pre>
        </div>
      `);
    }
    const combinedLinks = [
      ...links,
      ...artifacts.map((artifact) => ({ path: artifact.path, label: artifact.artifact_type }))
    ].filter((item) => item && item.path);
    if (combinedLinks.length) {
      parts.push(fileLinksBlock(combinedLinks));
    }
    return parts.length ? `<div class="event-summary">${parts.join("")}</div>` : "";
  }

  function fileLinksBlock(links) {
    if (!Array.isArray(links) || !links.length) {
      return "";
    }
    return `
      <div class="link-list">
        ${links.map((link) => `
          <div class="link-row">
            <span class="path-text">${escapeHtml(link.label || link.path)}${link.line ? `:${escapeHtml(link.line)}` : ""}</span>
            ${pathButton(vscode ? "Open File" : "Preview", link.path, link.line)}
          </div>
        `).join("")}
      </div>
    `;
  }

  function selectField(label, name, options, selected) {
    return `
      <label class="form-field">
        <span>${escapeHtml(label)}</span>
        <select name="${escapeAttr(name)}">
          ${options.map(([value, text]) => `
            <option value="${escapeAttr(value)}"${value === selected ? " selected" : ""}>${escapeHtml(text)}</option>
          `).join("")}
        </select>
      </label>
    `;
  }

  function kv(label, content) {
    return `<div class="kv"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(content))}</strong></div>`;
  }

  function statusChip(value) {
    const rawValue = String(value || "n/a");
    return `<span class="chip ${statusClass(rawValue)}">${escapeHtml(humanize(rawValue))}</span>`;
  }

  function statusClass(value) {
    const normalized = String(value || "").toLowerCase();
    if (["passed", "completed", "accepted_cleanly", "accepted_with_minor_edits", "accepted_with_major_edits", "closed"].includes(normalized)) {
      return "pass";
    }
    if (["failed", "timed_out", "rejected", "critical"].includes(normalized) || normalized.startsWith("exit_1")) {
      return "fail";
    }
    if (["partial", "not_reviewed", "review_skipped", "not_configured", "unverified"].includes(normalized)) {
      return "warn";
    }
    if (normalized.includes("ms") || normalized.startsWith("exit_0")) {
      return "info";
    }
    return "";
  }

  function selectedEventPayload(event) {
    if (event.source_payload_sanitized_json) {
      return event.source_payload_sanitized_json;
    }
    if (event.normalized_payload_json) {
      return event.normalized_payload_json;
    }
    return event;
  }

  function eventKey(event) {
    return String(event.id || event.sequence_number || "event");
  }

  function showModal(title, content) {
    elements.modalTitle.textContent = title;
    elements.modalContent.textContent = content;
    elements.modalBackdrop.hidden = false;
  }

  function closeModal() {
    elements.modalBackdrop.hidden = true;
    elements.modalContent.textContent = "";
  }

  function setSync(text) {
    elements.syncState.textContent = text;
  }

  function setReviewStatus(text) {
    const element = document.getElementById("reviewStatus");
    if (element) {
      element.textContent = text;
    }
  }

  function value(input) {
    if (input === null || input === undefined || input === "") {
      return "n/a";
    }
    return input;
  }

  function emptyToNull(input) {
    const value = String(input || "").trim();
    return value ? value : null;
  }

  function compact(input, maxLength) {
    const value = String(input || "");
    if (value.length <= maxLength) {
      return value;
    }
    return `${value.slice(0, Math.max(0, maxLength - 3))}...`;
  }

  function summaryText(input, maxLength, fallback) {
    const value = String(input || "").replace(/\s+/g, " ").trim();
    return compact(value || fallback, maxLength);
  }

  function humanize(input) {
    const value = String(input || "").replace(/_/g, " ");
    return value ? value[0].toUpperCase() + value.slice(1) : "";
  }

  function formatDate(input) {
    if (!input) {
      return "n/a";
    }
    const date = new Date(input);
    if (Number.isNaN(date.getTime())) {
      return String(input);
    }
    return date.toLocaleString();
  }

  function formatDuration(input) {
    if (input === null || input === undefined || input === "") {
      return "n/a";
    }
    const ms = Number(input);
    if (!Number.isFinite(ms)) {
      return String(input);
    }
    if (ms < 1000) {
      return `${Math.round(ms)} ms`;
    }
    if (ms < 60000) {
      return `${(ms / 1000).toFixed(1)} s`;
    }
    return `${(ms / 60000).toFixed(1)} min`;
  }

  function formatBytes(input) {
    if (input === null || input === undefined || input === "") {
      return "unknown_size";
    }
    const bytes = Number(input);
    if (!Number.isFinite(bytes)) {
      return String(input);
    }
    if (bytes < 1024) {
      return `${bytes} B`;
    }
    if (bytes < 1024 * 1024) {
      return `${(bytes / 1024).toFixed(1)} KB`;
    }
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  function escapeHtml(input) {
    return String(input)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function escapeAttr(input) {
    return escapeHtml(input);
  }
})();
