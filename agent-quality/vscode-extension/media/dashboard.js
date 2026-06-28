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
    deleting: false,
    details: null,
    pending: new Map(),
    query: "",
    runs: [],
    saveTimer: 0,
    selectedEventId: null,
    selectedRunId: initialRunId,
    viewMode: "chats"
  };
  const sidebarResize = {
    max: 720,
    min: 260,
    storageKey: "agentQuality.sidebarWidth"
  };
  const elements = {};

  document.addEventListener("DOMContentLoaded", init);

  function init() {
    elements.hostLabel = document.getElementById("hostLabel");
    elements.syncState = document.getElementById("syncState");
    elements.runCount = document.getElementById("runCount");
    elements.runList = document.getElementById("runList");
    elements.runSearch = document.getElementById("runSearch");
    elements.viewMode = document.getElementById("viewMode");
    elements.chatFilter = document.getElementById("chatFilter");
    elements.agentFilter = document.getElementById("agentFilter");
    elements.statusFilter = document.getElementById("statusFilter");
    elements.workspace = document.querySelector(".workspace");
    elements.sidebarResizer = document.getElementById("sidebarResizer");
    elements.detailPane = document.getElementById("detailPane");
    elements.modalBackdrop = document.getElementById("modalBackdrop");
    elements.modalTitle = document.getElementById("modalTitle");
    elements.modalContent = document.getElementById("modalContent");

    elements.hostLabel.textContent = vscode ? "VS Code" : "Browser";
    document.addEventListener("click", handleClick);
    document.addEventListener("input", handleInput);
    document.addEventListener("change", handleChange);
    window.addEventListener("message", handleHostMessage);
    initSidebarResize();
    loadRuns();
  }

  function initSidebarResize() {
    if (!elements.workspace || !elements.sidebarResizer) {
      return;
    }
    const savedWidth = Number(window.localStorage.getItem(sidebarResize.storageKey));
    if (Number.isFinite(savedWidth) && savedWidth > 0) {
      setSidebarWidth(savedWidth);
    }
    elements.sidebarResizer.addEventListener("pointerdown", startSidebarResize);
    elements.sidebarResizer.addEventListener("keydown", handleSidebarResizeKeydown);
  }

  function startSidebarResize(event) {
    if (event.button !== 0 || !elements.workspace) {
      return;
    }
    event.preventDefault();
    elements.sidebarResizer.setPointerCapture(event.pointerId);
    elements.workspace.classList.add("is-resizing");

    const move = (moveEvent) => {
      setSidebarWidth(widthFromPointer(moveEvent.clientX));
    };
    const stop = () => {
      elements.workspace.classList.remove("is-resizing");
      elements.sidebarResizer.removeEventListener("pointermove", move);
      elements.sidebarResizer.removeEventListener("pointerup", stop);
      elements.sidebarResizer.removeEventListener("pointercancel", stop);
      persistSidebarWidth();
    };

    elements.sidebarResizer.addEventListener("pointermove", move);
    elements.sidebarResizer.addEventListener("pointerup", stop);
    elements.sidebarResizer.addEventListener("pointercancel", stop);
  }

  function handleSidebarResizeKeydown(event) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      return;
    }
    event.preventDefault();
    const current = currentSidebarWidth();
    const step = event.shiftKey ? 40 : 16;
    let next = current;
    if (event.key === "ArrowLeft") {
      next = current - step;
    } else if (event.key === "ArrowRight") {
      next = current + step;
    } else if (event.key === "Home") {
      next = sidebarResize.min;
    } else if (event.key === "End") {
      next = maxSidebarWidth();
    }
    setSidebarWidth(next);
    persistSidebarWidth();
  }

  function widthFromPointer(clientX) {
    const rect = elements.workspace.getBoundingClientRect();
    return clientX - rect.left;
  }

  function setSidebarWidth(width) {
    const next = clamp(width, sidebarResize.min, maxSidebarWidth());
    elements.workspace.style.setProperty("--rail-width", `${Math.round(next)}px`);
    elements.sidebarResizer.setAttribute("aria-valuemin", String(sidebarResize.min));
    elements.sidebarResizer.setAttribute("aria-valuemax", String(maxSidebarWidth()));
    elements.sidebarResizer.setAttribute("aria-valuenow", String(Math.round(next)));
  }

  function currentSidebarWidth() {
    const value = getComputedStyle(elements.workspace).getPropertyValue("--rail-width");
    const parsed = Number.parseFloat(value);
    return Number.isFinite(parsed) ? parsed : 360;
  }

  function maxSidebarWidth() {
    const workspaceWidth = elements.workspace ? elements.workspace.getBoundingClientRect().width : 0;
    if (!workspaceWidth) {
      return sidebarResize.max;
    }
    return Math.max(sidebarResize.min, Math.min(sidebarResize.max, workspaceWidth - 280));
  }

  function persistSidebarWidth() {
    window.localStorage.setItem(sidebarResize.storageKey, String(Math.round(currentSidebarWidth())));
  }

  function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
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
    } else if (action === "deleteChat") {
      deleteSelectedChat(target.dataset.chatId);
    } else if (action === "copyTranscript") {
      copyTranscript("full");
    } else if (action === "copyCompactTranscript") {
      copyTranscript("compact");
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
    if (
      event.target === elements.statusFilter ||
      event.target === elements.agentFilter ||
      event.target === elements.chatFilter
    ) {
      renderRuns();
      return;
    }
    if (event.target === elements.viewMode) {
      state.viewMode = event.target.value;
      state.selectedRunId = null;
      state.details = null;
      loadRuns();
      return;
    }
    if (event.target.closest("#reviewForm")) {
      scheduleReviewSave();
    }
  }

  async function loadRuns() {
    setSync("Loading");
    try {
      const isChats = state.viewMode === "chats";
      const items = await request(isChats ? "loadSessions" : "loadRuns");
      state.runs = Array.isArray(items) ? items : (items.sessions || items.runs || []);
      if (!state.selectedRunId && state.runs.length) {
        state.selectedRunId = state.runs[0].id;
      }
      if (state.selectedRunId && !state.runs.some((run) => run.id === state.selectedRunId)) {
        state.selectedRunId = state.runs.length ? state.runs[0].id : null;
      }
      updateChatFilterOptions();
      updateAgentFilterOptions();
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

  function updateChatFilterOptions() {
    const sessions = new Set();
    state.runs.forEach((run) => {
      const sessionId = run.session_id || (state.viewMode === "chats" ? run.id : "");
      if (sessionId) {
        sessions.add(sessionId);
      }
    });
    const currentValue = elements.chatFilter.value;
    let html = '<option value="all">All chats</option>';
    Array.from(sessions).sort().forEach((session) => {
      html += `<option value="${escapeAttr(session)}">Chat: ${escapeHtml(session.substring(0, 8))}...</option>`;
    });
    elements.chatFilter.innerHTML = html;
    if (Array.from(sessions).includes(currentValue)) {
      elements.chatFilter.value = currentValue;
    } else {
      elements.chatFilter.value = "all";
    }
  }

  function updateAgentFilterOptions() {
    const agents = new Set();
    state.runs.forEach((run) => {
      if (run.agent_adapter) {
        agents.add(run.agent_adapter);
      }
    });
    const currentValue = elements.agentFilter.value;
    let html = '<option value="all">All agents</option>';
    Array.from(agents).sort().forEach((agent) => {
      html += `<option value="${escapeAttr(agent)}">${escapeHtml(agent)}</option>`;
    });
    elements.agentFilter.innerHTML = html;
    if (Array.from(agents).includes(currentValue)) {
      elements.agentFilter.value = currentValue;
    } else {
      elements.agentFilter.value = "all";
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
      const isChats = state.viewMode === "chats";
      state.details = await request(isChats ? "loadSessionDetails" : "loadRunDetails", isChats ? { session_id: runId } : { run_id: runId });
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
      elements.runList.innerHTML = `<div class="empty-copy">No ${state.viewMode === "chats" ? "chats" : "runs"} found.</div>`;
      return;
    }
    elements.runList.innerHTML = filtered.map((run) => {
      const active = run.id === state.selectedRunId ? " is-active" : "";
      const promptText = run.prompt || run.task_summary || "No prompt captured";
      const prompt = summaryText(promptText, 96, "No prompt captured");
      const meta = [
        run.started_at ? formatDate(run.started_at) : "",
        run.model || "",
        run.turn_count > 1 ? `${run.turn_count} turns` : ""
      ].filter(Boolean).join(" - ");
      const deleteButton = vscode && state.viewMode === "chats"
        ? `
          <button
            type="button"
            class="run-delete-button"
            data-action="deleteChat"
            data-chat-id="${escapeAttr(run.id)}"
            aria-label="Delete chat"
            title="Delete chat"
            ${state.deleting ? "disabled" : ""}
          >
            <svg aria-hidden="true" viewBox="0 0 24 24" focusable="false">
              <path d="M9 3h6l1 2h4v2H4V5h4l1-2Zm-2 6h10l-.7 11H7.7L7 9Zm3 2v7h2v-7h-2Zm4 0v7h2v-7h-2Z"></path>
            </svg>
          </button>
        `
        : "";
      return `
        <div class="run-card-row">
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
          ${deleteButton}
        </div>
      `;
    }).join("");
  }

  function filteredRuns() {
    const filter = elements.statusFilter.value;
    const agentFilter = elements.agentFilter.value;
    const chatFilter = elements.chatFilter.value;
    return state.runs.filter((run) => {
      const sessionId = run.session_id || (state.viewMode === "chats" ? run.id : "");
      const haystack = [
        run.id,
        sessionId,
        run.prompt || run.task_summary,
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
        if (run.verifier_status !== "passed") return false;
      } else if (filter === "failed") {
        if (run.verifier_status !== "failed" && run.agent_status !== "failed") return false;
      } else if (filter === "reviewed") {
        if (!run.human_status || ["not_reviewed", "review_skipped"].includes(run.human_status)) return false;
      } else if (filter === "unreviewed") {
        if (run.human_status && run.human_status !== "not_reviewed") return false;
      }
      if (agentFilter !== "all") {
        if (run.agent_adapter !== agentFilter) return false;
      }
      if (chatFilter !== "all") {
        if (sessionId !== chatFilter) return false;
      }
      return true;
    });
  }

  function renderDetail(loading) {
    if (loading) {
      elements.detailPane.innerHTML = `<div class="empty-state"><h2>Loading ${state.viewMode === "chats" ? "chat" : "run"}</h2><p>...</p></div>`;
      return;
    }
    const details = state.details;
    if (!details || (!details.run && !details.session)) {
      elements.detailPane.innerHTML = `<div class="empty-state"><h2>No ${state.viewMode === "chats" ? "chat" : "run"} selected</h2><p>No active selection.</p></div>`;
      return;
    }
    const run = details.run || details.session;
    const agentStatus = details.run ? (details.run.agent_status || "agent_unknown") : (details.turns.length ? (details.turns[details.turns.length - 1].run.agent_status || "agent_unknown") : "agent_unknown");
    const verifierStatus = details.run ? (details.run.verifier_status || "unverified") : (details.turns.length ? (details.turns[details.turns.length - 1].run.verifier_status || "unverified") : "unverified");
    const humanStatus = details.run ? (details.run.human_status || "not_reviewed") : (details.turns.length ? (details.turns[details.turns.length - 1].run.human_status || "not_reviewed") : "not_reviewed");
    const lifecycleStatus = details.run ? (details.run.lifecycle_status || "lifecycle_unknown") : (details.turns.length ? (details.turns[details.turns.length - 1].run.lifecycle_status || "lifecycle_unknown") : "lifecycle_unknown");
    
    const promptText = run.prompt || run.task_summary || "Details";
    const selectedTabId = `run-tab-${state.activeTab}`;
    const deleteChatButton = vscode && state.viewMode === "chats"
      ? `<button type="button" class="button danger" data-action="deleteChat"${state.deleting ? " disabled" : ""}>${state.deleting ? "Deleting..." : "Delete Chat"}</button>`
      : "";
    elements.detailPane.innerHTML = `
      <div class="detail-layout">
        <div class="detail-head">
          <div class="detail-title">
            <h2>${escapeHtml(summaryText(promptText, 104, "Details"))}</h2>
            <p>${escapeHtml(run.repository_path || "n/a")}</p>
            <div class="status-row">
              ${statusChip(agentStatus)}
              ${statusChip(verifierStatus)}
              ${statusChip(humanStatus)}
              ${statusChip(lifecycleStatus)}
            </div>
          </div>
          <div class="detail-actions">
            ${deleteChatButton}
            <button type="button" class="button ghost" data-action="copyTranscript">Copy Chat</button>
            <button type="button" class="button ghost" data-action="copyCompactTranscript">Copy Compact</button>
            <button type="button" class="button ghost" data-action="openDiff">Open Diff</button>
            <button type="button" class="button primary" data-action="setTab" data-tab="review">Review</button>
          </div>
        </div>
        <div class="tabs" role="tablist" aria-label="Views">
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
      return renderVerifiers(details.verifier_results || details.all_verifier_results || []);
    }
    if (state.activeTab === "artifacts") {
      return renderArtifacts(details.artifacts || details.all_artifacts || []);
    }
    if (state.activeTab === "timeline") {
      return renderTimeline(details.events || details.all_events || []);
    }
    if (state.activeTab === "review") {
      return renderReview(details);
    }
    return renderOverview(details);
  }

  function renderOverview(details) {
    if (details.turns) {
      // Sessions / Chats Overview Mode
      return `
        <div class="overview-stack">
          <div class="overview-primary">
            ${details.turns.map((turn, index) => {
              const run = turn.run;
              const outputs = turn.agent_outputs || [];
              const latestOutput = outputs.length ? outputs[outputs.length - 1] : null;
              const reasoningTrace = turn.reasoning_trace || [];
              const toolCalls = turn.tool_calls || [];
              return `
                <div class="chat-turn-card">
                  <div class="chat-turn-header">Turn ${index + 1} (${escapeHtml(run.id)})</div>
                  <section class="reading-section" aria-labelledby="prompt-heading-${run.id}">
                    <h3 id="prompt-heading-${run.id}" class="section-title">Prompt</h3>
                    <pre class="prompt-block reading-content">${escapeHtml(run.prompt || "No prompt captured.")}</pre>
                  </section>
                  <section class="reading-section" aria-labelledby="output-heading-${run.id}">
                    <h3 id="output-heading-${run.id}" class="section-title">Agent output</h3>
                    ${latestOutput ? outputBlock(latestOutput) : '<div class="empty-copy reading-empty">No agent output captured yet.</div>'}
                  </section>
                  <details class="chat-turn-details">
                    <summary>Reasoning & Tool Calls</summary>
                    <div style="padding-top: 10px;">
                      <section class="reading-section" aria-labelledby="reasoning-heading-${run.id}">
                        <h4 id="reasoning-heading-${run.id}" class="section-subtitle" style="font-size: 12px; margin-bottom: 8px;">Reasoning trace</h4>
                        ${reasoningTraceBlock(reasoningTrace)}
                      </section>
                      <section class="reading-section" aria-labelledby="tools-heading-${run.id}" style="margin-top: 16px;">
                        <h4 id="tools-heading-${run.id}" class="section-subtitle" style="font-size: 12px; margin-bottom: 8px;">Tool calls</h4>
                        ${toolCallsBlock(toolCalls)}
                      </section>
                    </div>
                  </details>
                </div>
              `;
            }).join("")}
          </div>
          <details class="overview-secondary session-secondary">
            <summary>Session details</summary>
            <div class="overview-grid">
              ${kv("Started", formatDate(details.session.started_at))}
              ${kv("Ended", formatDate(details.session.ended_at))}
              ${kv("Session ID", value(details.session.id))}
              ${kv("Repository", value(details.session.repository_path))}
              ${kv("Turns count", details.turns.length)}
            </div>
          </details>
        </div>
      `;
    }

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
    const run = details.run || (details.turns && details.turns.length ? details.turns[details.turns.length - 1].run : null);
    if (!run) {
      return '<div class="empty-copy">No run to review.</div>';
    }
    const review = details.run ? ((details.human_reviews || [])[0] || {}) : (details.turns && details.turns.length ? ((details.turns[details.turns.length - 1].human_reviews || [])[0] || {}) : {});
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
    if (!form || !state.details) {
      return;
    }
    const run = state.details.run || (state.details.turns && state.details.turns.length ? state.details.turns[state.details.turns.length - 1].run : null);
    if (!run) {
      return;
    }
    window.clearTimeout(state.saveTimer);
    setReviewStatus("Saving");
    try {
      const formData = new FormData(form);
      const payload = {
        run_id: run.id,
        outcome: String(formData.get("outcome") || "not_reviewed"),
        primary_category: emptyToNull(formData.get("primary_category")),
        severity: emptyToNull(formData.get("severity")),
        notes: String(formData.get("notes") || ""),
        confidence: Number(formData.get("confidence")),
        critical_sequence: emptyToNull(formData.get("critical_sequence"))
      };
      const response = await request("saveReview", payload);
      const review = response.review || response;
      if (state.details.run) {
        state.details.human_reviews = [review];
        state.details.run.human_status = review.outcome;
      } else {
        const lastTurn = state.details.turns[state.details.turns.length - 1];
        lastTurn.human_reviews = [review];
        lastTurn.run.human_status = review.outcome;
      }
      const runItem = state.runs.find((item) => item.id === (state.details.run ? state.details.run.id : state.details.session.id));
      if (runItem) {
        runItem.human_status = review.outcome;
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
    const run = state.details && (state.details.run || (state.details.turns && state.details.turns.length ? state.details.turns[state.details.turns.length - 1].run : null));
    if (!run) {
      return;
    }
    const runId = run.id;
    if (vscode) {
      await request("openDiff", { run_id: runId });
      return;
    }
    const artifacts = state.details.artifacts || state.details.all_artifacts || [];
    const patch = artifacts.find((artifact) => ["final_patch", "diff", "patch"].includes(artifact.artifact_type));
    if (patch && patch.path) {
      await previewPath(patch.path);
      return;
    }
    showModal("Diff", `aq diff ${runId}`);
  }

  async function deleteSelectedChat(chatId) {
    chatId = chatId || state.selectedRunId;
    if (!vscode || state.viewMode !== "chats" || !chatId || state.deleting) {
      return;
    }
    const currentIndex = state.runs.findIndex((run) => run.id === chatId);
    const nextChatId = currentIndex >= 0
      ? ((state.runs[currentIndex + 1] && state.runs[currentIndex + 1].id) ||
        (state.runs[currentIndex - 1] && state.runs[currentIndex - 1].id) || null)
      : null;
    state.deleting = true;
    renderDetail();
    setSync("Deleting");
    try {
      const result = await request("deleteChat", { chat_id: chatId });
      if (!result.deleted) {
        state.deleting = false;
        renderDetail();
        setSync("Idle");
        return;
      }
      state.deleting = false;
      state.selectedRunId = nextChatId;
      state.selectedEventId = null;
      state.details = null;
      await loadRuns();
    } catch (error) {
      state.deleting = false;
      renderDetail();
      setSync("Error");
      showModal("Delete Chat Failed", error.message || String(error));
    }
  }

  async function copyTranscript(mode) {
    if (!state.details) {
      return;
    }
    const isCompact = mode === "compact";
    const text = buildTranscriptMarkdown(state.details, {
      compact: isCompact,
      textLimit: isCompact ? 6000 : 60000,
      toolLimit: isCompact ? 2500 : 12000
    });
    setSync("Copying");
    try {
      if (vscode) {
        await request("copyText", {
          text,
          label: isCompact ? "compact chat transcript" : "chat transcript"
        });
      } else if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        showModal(isCompact ? "Compact Chat Transcript" : "Chat Transcript", text);
        setSync("Idle");
        return;
      }
      setSync("Copied");
      window.setTimeout(() => setSync("Idle"), 1400);
    } catch (error) {
      setSync("Copy failed");
      showModal("Copy failed", error.message || String(error));
    }
  }

  async function request(command, payload) {
    if (vscode) {
      return requestVsCode(command, payload || {});
    }
    if (command === "loadRuns") {
      const response = await fetchJson("/v1/ui/api/runs");
      return response;
    }
    if (command === "loadSessions") {
      const response = await fetchJson("/v1/ui/api/sessions");
      return response;
    }
    if (command === "loadRunDetails") {
      return fetchJson(`/v1/ui/api/run/${encodeURIComponent(payload.run_id)}`);
    }
    if (command === "loadSessionDetails") {
      return fetchJson(`/v1/ui/api/session/${encodeURIComponent(payload.session_id)}`);
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
      if (command !== "deleteChat") {
        window.setTimeout(() => {
          if (state.pending.has(requestId)) {
            state.pending.delete(requestId);
            reject(new Error(`${command} timed out`));
          }
        }, 30000);
      }
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

  function buildTranscriptMarkdown(details, options) {
    const turns = details.turns || [{
      run: details.run,
      agent_outputs: details.agent_outputs || [],
      reasoning_trace: details.reasoning_trace || [],
      tool_calls: details.tool_calls || [],
      verifier_results: details.verifier_results || [],
      human_reviews: details.human_reviews || []
    }];
    const subject = details.session || details.run || {};
    const lines = [
      "# Agent Quality Chat Transcript",
      "",
      `- Export: ${options.compact ? "compact" : "full bounded"}`,
      `- Chat ID: ${subject.id || "n/a"}`,
      `- Repository: ${subject.repository_path || "n/a"}`,
      `- Started: ${formatDate(subject.started_at)}`,
      `- Ended: ${formatDate(subject.ended_at || subject.completed_at)}`,
      `- Turns: ${turns.length}`,
      "",
      "> Note: this export includes captured prompts, assistant outputs, emitted reasoning summaries, and tool calls. Private chain-of-thought is not available to Agent Quality.",
      ""
    ];

    turns.forEach((turn, index) => {
      const run = turn.run || {};
      lines.push(`## Turn ${index + 1}`);
      lines.push("");
      lines.push(`- Run ID: ${run.id || "n/a"}`);
      lines.push(`- Model: ${run.model || "n/a"}`);
      lines.push(`- Agent: ${run.agent_adapter || "n/a"}`);
      lines.push(`- Status: ${[run.agent_status, run.verifier_status, run.human_status].filter(Boolean).join(" / ") || "n/a"}`);
      lines.push("");
      pushSection(lines, "Prompt", run.prompt || "No prompt captured.", options.textLimit);

      const outputs = turn.agent_outputs || [];
      if (outputs.length) {
        outputs.forEach((output, outputIndex) => {
          const suffix = outputs.length > 1 ? ` ${outputIndex + 1}` : "";
          pushSection(lines, `Agent output${suffix}`, output.text || "", options.textLimit);
        });
      } else {
        pushSection(lines, "Agent output", "No agent output captured.", options.textLimit);
      }

      const trace = turn.reasoning_trace || [];
      if (trace.length) {
        lines.push("### Reasoning summaries");
        lines.push("");
        trace.forEach((entry, traceIndex) => {
          lines.push(`#### ${traceIndex + 1}. ${entry.kind || "summary"}${entry.occurred_at ? ` - ${formatDate(entry.occurred_at)}` : ""}`);
          lines.push("");
          lines.push(fence(compact(entry.text || "", options.toolLimit)));
          lines.push("");
        });
      }

      const calls = turn.tool_calls || [];
      if (calls.length) {
        lines.push("### Tool calls");
        lines.push("");
        calls.forEach((call, callIndex) => {
          lines.push(`#### ${callIndex + 1}. ${call.tool_name || "tool"}`);
          lines.push("");
          lines.push(`- Category: ${call.tool_category || "n/a"}`);
          lines.push(`- Status: ${call.status || "n/a"}`);
          lines.push(`- Occurred: ${formatDate(call.occurred_at)}`);
          pushSection(lines, "Input", stringifyForTranscript(call.input), options.toolLimit);
          pushSection(lines, "Output", stringifyForTranscript(call.output), options.toolLimit);
        });
      }
    });

    return lines.join("\n").replace(/\n{4,}/g, "\n\n\n").trim() + "\n";
  }

  function pushSection(lines, title, content, limit) {
    lines.push(`### ${title}`);
    lines.push("");
    lines.push(fence(compact(content || "n/a", limit)));
    lines.push("");
  }

  function fence(content) {
    const text = String(content || "");
    const marker = text.includes("```") ? "````" : "```";
    return `${marker}\n${text}\n${marker}`;
  }

  function stringifyForTranscript(value) {
    if (value === null || value === undefined || value === "") {
      return "n/a";
    }
    if (typeof value === "string") {
      return value;
    }
    return JSON.stringify(value, null, 2);
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
