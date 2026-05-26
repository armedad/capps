const appsEl = document.getElementById("apps");
const refreshAllBtn = document.getElementById("refresh");
const updatedEl = document.getElementById("updated");

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

function renderCard(app) {
  const li = document.createElement("li");
  li.className = `app-card${app.external ? " external" : ""}`;
  li.dataset.appId = app.id;

  const st = statusLabel(app);
  const title = app.external
    ? `<h2>${escapeHtml(app.name)} <span class="badge">dependency</span></h2>`
    : `<h2><a href="${app.url}" target="_blank" rel="noopener">${escapeHtml(app.name)}</a></h2>`;

  li.innerHTML = `
    ${title}
    <p class="app-desc">${escapeHtml(app.description)}</p>
    <p class="app-meta">Port ${app.port} · ${app.external ? `<span>${escapeHtml(app.health_check_url || app.url)}</span>` : `<a href="${app.url}" target="_blank" rel="noopener">${escapeHtml(app.url)}</a>`}</p>
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

async function refreshAll() {
  const cardButtons = [...appsEl.querySelectorAll(".app-card button")];
  refreshAllBtn.disabled = true;
  cardButtons.forEach((b) => { b.disabled = true; });
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
    refreshAllBtn.disabled = false;
    refreshAllBtn.textContent = "Refresh all";
    cardButtons.forEach((b) => { b.disabled = false; });
  }
}

async function action(kind, id, btn) {
  const app = appState.get(id);
  if (app?.external && kind !== "refresh") {
    alert(app.external ? "Not managed from this dashboard (external service)" : NOT_IMPLEMENTED);
    return;
  }
  if (app?.stop_stub && (kind === "stop" || kind === "restart")) {
    alert(NOT_IMPLEMENTED);
    return;
  }

  const labels = {
    start: `Starting${POLL_HINT}…`,
    stop: `Stopping${POLL_HINT}…`,
    restart: `Restarting${POLL_HINT}…`,
  };
  const paths = {
    start: `/api/apps/${id}/start`,
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

async function init() {
  try {
    const { apps } = await fetchJson("/api/apps/catalog");
    for (const app of apps) {
      appState.set(app.id, { ...app, running: null });
    }
    renderAll();
  } catch (err) {
    appsEl.innerHTML = `<li class="app-card"><p>Error: ${escapeHtml(err.message)}</p></li>`;
  }
}

init();
