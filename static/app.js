/* CSV Data Cleaning Pipeline — frontend logic (no framework, no build step). */
"use strict";

const state = {
  sessionId: null,
  profile: null,
  pipeline: [],
  previewSource: "current",
};

const $ = (sel) => document.querySelector(sel);

/* ---------------------------------------------------------------- helpers */

async function api(path, options = {}) {
  const res = await fetch(path, options);
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON error body */ }
  if (!res.ok) {
    const detail = body && body.detail ? body.detail : `${res.status} ${res.statusText}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

let toastTimer = null;
function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("error", isError);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, isError ? 6000 : 3500);
}

function fmt(value) {
  if (value === null || value === undefined) return "–";
  if (typeof value === "number" && !Number.isInteger(value)) {
    return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  return String(value);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/* ------------------------------------------------------------- rendering */

function applyState(data) {
  state.sessionId = data.session_id;
  state.profile = data.profile;
  state.pipeline = data.pipeline || [];

  $("#upload-panel").hidden = true;
  $("#workspace").hidden = false;
  const badge = $("#file-badge");
  badge.textContent = data.filename;
  badge.hidden = false;

  renderWarnings(data.warnings || []);
  renderProfile();
  renderPipeline();
  updateColumnSelects();
  updateDownloadLinks();
  refreshPreview();
}

function renderWarnings(warnings) {
  const box = $("#warnings");
  box.hidden = warnings.length === 0;
  box.textContent = warnings.join(" · ");
}

function renderProfile() {
  const p = state.profile;

  const chips = $("#stat-chips");
  chips.replaceChildren(
    el("span", "chip", `${p.rows.toLocaleString()} rows`),
    el("span", "chip", `${p.cols} columns`),
    el("span", p.duplicate_rows ? "chip chip-warn" : "chip",
      `${p.duplicate_rows} duplicate rows`),
  );

  const table = $("#profile-table");
  table.replaceChildren();
  const headers = ["Column", "Type", "Nulls", "Unique", "Min", "Max", "Mean", "Outliers"];
  const thead = el("thead");
  const headRow = el("tr");
  headers.forEach((h, i) => headRow.appendChild(el("th", i >= 2 ? "num" : "", h)));
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = el("tbody");
  for (const col of p.columns) {
    const tr = el("tr");
    tr.appendChild(el("td", "col-name", col.name));

    const typeCell = el("td");
    typeCell.appendChild(el("span", "type-badge", col.inferred_type));
    tr.appendChild(typeCell);

    const nullCell = el("td", "num");
    nullCell.appendChild(col.nulls
      ? el("span", "null-pill", `${col.nulls} (${col.null_pct}%)`)
      : el("span", "zero", "0"));
    tr.appendChild(nullCell);

    tr.appendChild(el("td", "num", fmt(col.unique)));
    tr.appendChild(el("td", "num", fmt(col.min)));
    tr.appendChild(el("td", "num", fmt(col.max)));
    tr.appendChild(el("td", "num", fmt(col.mean)));
    const outCell = el("td", "num");
    outCell.appendChild(col.outliers
      ? el("span", "null-pill", String(col.outliers))
      : el("span", "zero", col.outliers === null ? "–" : "0"));
    tr.appendChild(outCell);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
}

function renderPipeline() {
  const list = $("#pipeline-list");
  list.replaceChildren();
  if (state.pipeline.length === 0) {
    list.appendChild(el("li", "pipeline-empty", "No steps applied yet."));
    return;
  }
  state.pipeline.forEach((step, i) => {
    const li = el("li");
    li.appendChild(el("span", "step-index", String(i + 1)));
    const body = el("div", "step-body");
    const paramText = Object.keys(step.params || {}).length
      ? " " + JSON.stringify(step.params)
      : "";
    const opLine = el("div");
    opLine.appendChild(el("span", "step-op", step.op));
    if (paramText) opLine.appendChild(el("span", "step-params", paramText));
    body.appendChild(opLine);
    body.appendChild(el("div", "step-summary", step.summary));
    li.appendChild(body);
    list.appendChild(li);
  });
}

function updateColumnSelects() {
  const cols = state.profile.columns;
  const fill = (select, items) => {
    const previous = select.value;
    select.replaceChildren(...items.map((c) => new Option(c, c)));
    if (items.includes(previous)) select.value = previous;
  };
  const names = cols.map((c) => c.name);
  fill($("#coerce-col"), names);
  fill($("#null-col"), names);
  const numericish = cols
    .filter((c) => ["integer", "float"].includes(c.inferred_type))
    .map((c) => c.name);
  fill($("#outlier-col"), numericish.length ? numericish : names);
}

function updateDownloadLinks() {
  $("#download-csv").href = `/api/sessions/${state.sessionId}/download`;
  $("#download-pipeline").href = `/api/sessions/${state.sessionId}/pipeline`;
}

async function refreshPreview() {
  if (!state.sessionId) return;
  const data = await api(
    `/api/sessions/${state.sessionId}/preview?source=${state.previewSource}`
  );
  $("#preview-count").textContent =
    `— ${state.previewSource === "current" ? "after" : "before"}, ` +
    `showing ${data.shown_rows} of ${data.total_rows.toLocaleString()} rows`;

  const table = $("#preview-table");
  table.replaceChildren();
  const thead = el("thead");
  const headRow = el("tr");
  data.columns.forEach((c) => headRow.appendChild(el("th", "", c)));
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = el("tbody");
  for (const row of data.rows) {
    const tr = el("tr");
    for (const cell of row) {
      tr.appendChild(cell === null
        ? el("td", "null-cell", "null")
        : el("td", typeof cell === "number" ? "num" : "", String(cell)));
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
}

/* --------------------------------------------------------------- actions */

async function uploadFile(file) {
  const form = new FormData();
  form.append("file", file, file.name);
  try {
    const data = await api("/api/upload", { method: "POST", body: form });
    applyState(data);
    toast(`Loaded ${file.name}: ${data.profile.rows} rows × ${data.profile.cols} columns`);
  } catch (err) {
    toast(err.message, true);
  }
}

async function loadSample() {
  try {
    const data = await api("/api/sample", { method: "POST" });
    applyState(data);
    toast(`Loaded sample: ${data.profile.rows} rows × ${data.profile.cols} columns`);
  } catch (err) {
    toast(err.message, true);
  }
}

async function applyStep(op, params = {}) {
  try {
    const data = await api(`/api/sessions/${state.sessionId}/steps`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ op, params }),
    });
    applyState(data);
    toast(data.summary);
  } catch (err) {
    toast(err.message, true);
  }
}

async function resetPipeline() {
  try {
    const data = await api(`/api/sessions/${state.sessionId}/reset`, { method: "POST" });
    applyState(data);
    toast(data.summary);
  } catch (err) {
    toast(err.message, true);
  }
}

/* ---------------------------------------------------------------- wiring */

function init() {
  $("#load-sample").addEventListener("click", loadSample);

  const fileInput = $("#file-input");
  fileInput.addEventListener("change", () => {
    if (fileInput.files.length) uploadFile(fileInput.files[0]);
    fileInput.value = "";
  });

  const dropzone = $("#dropzone");
  ["dragenter", "dragover"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.add("dragover");
    })
  );
  ["dragleave", "drop"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.remove("dragover");
    })
  );
  dropzone.addEventListener("drop", (e) => {
    if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]);
  });

  document.querySelectorAll("[data-quick-op]").forEach((btn) =>
    btn.addEventListener("click", () => applyStep(btn.dataset.quickOp))
  );

  $("#apply-coerce").addEventListener("click", () =>
    applyStep("coerce_type", {
      column: $("#coerce-col").value,
      target: $("#coerce-target").value,
    })
  );

  const strategySelect = $("#null-strategy");
  strategySelect.addEventListener("change", () => {
    $("#null-fill-value").hidden = strategySelect.value !== "value";
  });
  $("#apply-nulls").addEventListener("click", () => {
    const params = {
      column: $("#null-col").value,
      strategy: strategySelect.value,
    };
    if (params.strategy === "value") {
      const raw = $("#null-fill-value").value;
      if (raw === "") { toast("Enter a fill value first", true); return; }
      params.fill_value = raw;
    }
    applyStep("handle_nulls", params);
  });

  $("#apply-outliers").addEventListener("click", () =>
    applyStep("handle_outliers", {
      column: $("#outlier-col").value,
      method: $("#outlier-method").value,
    })
  );

  $("#reset-pipeline").addEventListener("click", resetPipeline);

  document.querySelectorAll(".seg-btn").forEach((btn) =>
    btn.addEventListener("click", () => {
      document.querySelectorAll(".seg-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.previewSource = btn.dataset.source;
      refreshPreview().catch((err) => toast(err.message, true));
    })
  );
}

init();
