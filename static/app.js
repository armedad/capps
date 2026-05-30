const appsEl = document.getElementById("apps");
const refreshAllBtn = document.getElementById("refresh");
const startAllBtn = document.getElementById("start-all");
const stopAllBtn = document.getElementById("stop-all");
const updatedEl = document.getElementById("updated");
const footerButtons = () => [
  refreshAllBtn,
  startAllBtn,
  stopAllBtn,
  ...appsEl.querySelectorAll(".app-card button"),
];

const NOT_IMPLEMENTED = "not yet implemented";
const POLL_HINT = " (up to 60s)";

/** @type {Map<string, object>} */
const appState = new Map();

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = data.detail || data.message || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function statusLabel(app) {
  if (app.running === undefined || app.running === null) {
    return { class: "unknown", text: "Unknown" };
  }
  return app.running
    ? { class: "up", text: "Running" }
    : { class: "down", text: "Stopped" };
}

function showResult(appId, success, message) {
  const li = appsEl.querySelector(`[data-app-id="${appId}"]`);
  if (!li) return;
  let el = li.querySelector('[data-role="result"]');
  if (!el) {
    el = document.createElement("p");
    el.className = "app-result";
    el.dataset.role = "result";
    const actions = li.querySelector(".app-actions");
    actions.parentNode.insertBefore(el, actions);
  }
  el.hidden = false;
  el.className = `app-result ${success ? "ok" : "fail"}`;
  el.textContent = message;
}

function setActionBusy(appId, activeBtn, busy) {
  const li = appsEl.querySelector(`[data-app-id="${appId}"]`);
  if (li) li.classList.toggle("is-busy", busy);
  if (activeBtn) activeBtn.disabled = busy;
}

function applyAppData(app) {
  appState.set(app.id, app);
  if (appsEl.querySelector(`[data-app-id="${app.id}"]`)) {
    updateCard(app);
  } else {
    renderAll();
  }
}

function handleActionResponse(data) {
  const app = data.app || data;
  if (app.id) applyAppData(app);
  if (data.message) {
    showResult(data.id, data.success !== false, data.message);
  }
  if (data.success === false && data.not_implemented) {
    alert(data.message || NOT_IMPLEMENTED);
  } else if (data.success === false) {
    alert(data.message || "Action failed");
  }
}

function appTitleHtml(app) {
  if (app.external) {
    return `<h2>${escapeHtml(app.name)} <span class="badge">dependency</span></h2>`;
  }
  if (app.url) {
    return `<h2><a href="${app.url}" target="_blank" rel="noopener">${escapeHtml(app.name)}</a></h2>`;
  }
  return `<h2>${escapeHtml(app.name)}</h2>`;
}

function appMetaHtml(app) {
  const health = escapeHtml(app.health_check_url || "");
  if (app.health_probe === "process" || !app.url) {
    return `<p class="app-meta health-check"><span>${health}</span></p>`;
  }
  if (app.external) {
    return `<p class="app-meta">Port ${app.port} · <span>${health}</span></p>`;
  }
  return `<p class="app-meta">Port ${app.port} · <a href="${app.url}" target="_blank" rel="noopener">${escapeHtml(app.url)}</a></p>`;
}

function renderCard(app) {
  const li = document.createElement("li");
  li.className = `app-card${app.external ? " external" : ""}`;
  li.dataset.appId = app.id;

  const st = statusLabel(app);

  li.innerHTML = `
    ${appTitleHtml(app)}
    <p class="app-desc">${escapeHtml(app.description)}</p>
    ${appMetaHtml(app)}
    <p class="app-result" data-role="result"></p>
    <div class="app-actions">
      <span class="status ${st.class}" data-role="status">${st.text}</span>
      <div class="btn-row" data-role="buttons"></div>
    </div>
  `;

  const buttons = li.querySelector('[data-role="buttons"]');

  const refreshBtn = document.createElement("button");
  refreshBtn.type = "button";
  refreshBtn.textContent = "Refresh";
  refreshBtn.addEventListener("click", () => refreshOne(app.id, refreshBtn));
  buttons.appendChild(refreshBtn);

  if (app.running === false && app.launch_available) {
    const startBtn = document.createElement("button");
    startBtn.type = "button";
    startBtn.className = "primary";
    startBtn.textContent = "Start";
    startBtn.addEventListener("click", () => action("start", app.id, startBtn));
    buttons.appendChild(startBtn);
  }

  if (app.running === false && app.start_debug_available) {
    const debugBtn = document.createElement("button");
    debugBtn.type = "button";
    debugBtn.className = "debug";
    debugBtn.textContent = "Start debug";
    debugBtn.addEventListener("click", () => action("start-debug", app.id, debugBtn));
    buttons.appendChild(debugBtn);
  }

  const stopBtn = document.createElement("button");
  stopBtn.type = "button";
  stopBtn.textContent = "Stop";
  stopBtn.addEventListener("click", () => action("stop", app.id, stopBtn));
  buttons.appendChild(stopBtn);

  const restartBtn = document.createElement("button");
  restartBtn.type = "button";
  restartBtn.textContent = "Restart";
  restartBtn.addEventListener("click", () => action("restart", app.id, restartBtn));
  buttons.appendChild(restartBtn);

  return li;
}

function updateCard(app) {
  const li = appsEl.querySelector(`[data-app-id="${app.id}"]`);
  if (!li) return;
  const st = statusLabel(app);
  const statusEl = li.querySelector('[data-role="status"]');
  statusEl.className = `status ${st.class}`;
  statusEl.textContent = st.text;

  const buttons = li.querySelector('[data-role="buttons"]');
  const existingStart = buttons.querySelector("button.primary");
  if (app.running === false && app.launch_available && !existingStart) {
    const startBtn = document.createElement("button");
    startBtn.type = "button";
    startBtn.className = "primary";
    startBtn.textContent = "Start";
    startBtn.addEventListener("click", () => action("start", app.id, startBtn));
    buttons.insertBefore(startBtn, buttons.children[1] || null);
  } else if (app.running !== false && existingStart) {
    existingStart.remove();
  }

  const existingDebug = buttons.querySelector("button.debug");
  if (app.running === false && app.start_debug_available && !existingDebug) {
    const debugBtn = document.createElement("button");
    debugBtn.type = "button";
    debugBtn.className = "debug";
    debugBtn.textContent = "Start debug";
    debugBtn.addEventListener("click", () => action("start-debug", app.id, debugBtn));
    const startBtn = buttons.querySelector("button.primary");
    if (startBtn && startBtn.nextSibling) {
      buttons.insertBefore(debugBtn, startBtn.nextSibling);
    } else if (startBtn) {
      buttons.appendChild(debugBtn);
    } else {
      buttons.insertBefore(debugBtn, buttons.children[1] || null);
    }
  } else if ((app.running !== false || !app.start_debug_available) && existingDebug) {
    existingDebug.remove();
  }
}

function renderAll() {
  const apps = [...appState.values()];
  if (apps.length === 0) {
    appsEl.innerHTML =
      '<li class="app-card placeholder"><p>Click <strong>Refresh all</strong> to check app status.</p></li>';
    return;
  }
  appsEl.replaceChildren(...apps.map(renderCard));
}

async function refreshOne(id, btn) {
  setActionBusy(id, btn, true);
  if (btn) btn.textContent = `Checking${POLL_HINT}…`;
  try {
    const data = await fetchJson(`/api/apps/${id}`);
    handleActionResponse(data);
    updatedEl.textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    showResult(id, false, err.message || String(err));
    alert(err.message || String(err));
  } finally {
    setActionBusy(id, btn, false);
    if (btn) btn.textContent = "Refresh";
  }
}

function summarizeBulk(action, results) {
  const started = results.filter((r) => r.action === "start" && r.success && !r.skipped).length;
  const skipped = results.filter((r) => r.skipped).length;
  const failed = results.filter((r) => !r.success && !r.skipped).length;
  const parts = [];
  if (action === "start-all") {
    if (started) parts.push(`${started} started`);
    if (skipped) parts.push(`${skipped} skipped`);
  } else {
    const stopped = results.filter((r) => r.action === "stop" && r.success && !r.skipped).length;
    if (stopped) parts.push(`${stopped} stopped`);
    if (skipped) parts.push(`${skipped} skipped`);
  }
  if (failed) parts.push(`${failed} failed`);
  return parts.join(", ") || "Nothing to do";
}

async function bulkAction(kind, btn) {
  const paths = {
    "start-all": "/api/apps/start-all",
    "stop-all": "/api/apps/stop-all",
  };
  const labels = {
    "start-all": `Starting all${POLL_HINT}…`,
    "stop-all": `Stopping all${POLL_HINT}…`,
  };

  footerButtons().forEach((b) => { b.disabled = true; });
  const prev = btn.textContent;
  btn.textContent = labels[kind];
  try {
    const data = await fetchJson(paths[kind], { method: "POST" });
    for (const app of data.apps || []) applyAppData(app);
    for (const r of data.results || []) {
      if (r.message) showResult(r.id, r.success !== false, r.message);
    }
    updatedEl.textContent = `${summarizeBulk(kind, data.results || [])} · ${new Date().toLocaleTimeString()}`;
    const failures = (data.results || []).filter((r) => !r.success && !r.skipped);
    if (failures.length) {
      alert(
        failures.map((r) => `${r.id}: ${r.message}`).join("\n") || "Some actions failed",
      );
    }
  } catch (err) {
    alert(err.message || String(err));
  } finally {
    footerButtons().forEach((b) => { b.disabled = false; });
    btn.textContent = prev;
  }
}

async function refreshAll() {
  footerButtons().forEach((b) => { b.disabled = true; });
  refreshAllBtn.textContent = `Checking all${POLL_HINT}…`;
  try {
    const { apps, results } = await fetchJson("/api/apps");
    for (const app of apps) applyAppData(app);
    if (results) {
      for (const r of results) {
        if (r.message) showResult(r.id, r.success !== false, r.message);
      }
    }
    updatedEl.textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    appsEl.innerHTML = `<li class="app-card"><p>Error: ${escapeHtml(err.message)}</p></li>`;
  } finally {
    footerButtons().forEach((b) => { b.disabled = false; });
    refreshAllBtn.textContent = "Refresh all";
  }
}

async function action(kind, id, btn) {
  const app = appState.get(id);
  if (app?.external && kind !== "refresh") {
    alert(app.external ? "Not managed from this dashboard (external service)" : NOT_IMPLEMENTED);
    return;
  }
  if (app?.stop_stub && (kind === "stop" || kind === "restart")) {
    alert(app.external ? "Not managed from this dashboard (external service)" : NOT_IMPLEMENTED);
    return;
  }
  if ((kind === "stop" || kind === "restart") && app && !app.stop_available) {
    alert(NOT_IMPLEMENTED);
    return;
  }

  const labels = {
    start: `Starting${POLL_HINT}…`,
    "start-debug": `Starting debug${POLL_HINT}…`,
    stop: `Stopping${POLL_HINT}…`,
    restart: `Restarting${POLL_HINT}…`,
  };
  const paths = {
    start: `/api/apps/${id}/start`,
    "start-debug": `/api/apps/${id}/start-debug`,
    stop: `/api/apps/${id}/stop`,
    restart: `/api/apps/${id}/restart`,
  };

  setActionBusy(id, btn, true);
  const prev = btn.textContent;
  btn.textContent = labels[kind];
  try {
    const data = await fetchJson(paths[kind], { method: "POST" });
    if (data.not_implemented) {
      alert(data.message || NOT_IMPLEMENTED);
      return;
    }
    handleActionResponse(data);
    if (data.success === false) {
      alert(data.message || "Action failed");
    }
  } catch (err) {
    showResult(id, false, err.message || String(err));
    alert(err.message || String(err));
  } finally {
    setActionBusy(id, btn, false);
    btn.textContent = prev;
  }
}

refreshAllBtn.addEventListener("click", refreshAll);
startAllBtn.addEventListener("click", () => bulkAction("start-all", startAllBtn));
stopAllBtn.addEventListener("click", () => bulkAction("stop-all", stopAllBtn));

async function init() {
  try {
    const { apps } = await fetchJson("/api/apps/catalog");
    for (const app of apps) {
      appState.set(app.id, { ...app, running: null });
    }
    renderAll();
    await refreshAll();
  } catch (err) {
    appsEl.innerHTML = `<li class="app-card"><p>Error: ${escapeHtml(err.message)}</p></li>`;
  }
}

init();
