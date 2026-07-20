const $ = (selector) => document.querySelector(selector);
const state = { data: null };
const googleLabels = {
  "google.gmail": "Gmail",
  "google.calendar": "Calendar",
  "google.contacts": "Contacts",
  "google.drive": "Drive",
};

function cookie(name) {
  const row = document.cookie.split("; ").find((value) => value.startsWith(`${name}=`));
  return row ? decodeURIComponent(row.split("=").slice(1).join("=")) : "";
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (options.method && options.method !== "GET") headers["X-Recall-CSRF"] = cookie("recall_admin_csrf");
  const response = await fetch(path, { credentials: "same-origin", ...options, headers });
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.error || "request_failed");
    error.status = response.status;
    throw error;
  }
  return payload;
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  window.setTimeout(() => node.classList.remove("show"), 2600);
}

function renderBrains() {
  const target = $("#brain-list");
  target.replaceChildren();
  state.data.brains.forEach((brain, index) => {
    const card = document.createElement("article");
    card.className = "brain-card";
    card.dataset.index = String(index + 1).padStart(2, "0");
    card.innerHTML = `
      <span class="brain-kind">${brain.brain_kind}</span>
      <h3>${escapeText(brain.display_name)}</h3>
      <p>${escapeText(brain.slug)} · ${escapeText(brain.permission)} access</p>`;
    target.append(card);
  });
}

function escapeText(value) {
  const node = document.createElement("span");
  node.textContent = String(value);
  return node.innerHTML;
}

function brainOptions() {
  return state.data.brains.map((brain) =>
    `<option value="${escapeText(brain.tenant_id)}">${escapeText(brain.display_name)}</option>`
  ).join("");
}

function renderGoogle() {
  const target = $("#google-routes");
  target.replaceChildren();
  const available = state.data.providers.some((item) => item.id === "google");
  Object.entries(googleLabels).forEach(([connectorId, label]) => {
    const row = document.createElement("div");
    row.className = "route-row";
    row.dataset.connector = connectorId;
    row.innerHTML = `
      <strong>${label}</strong>
      <label><span class="sr-only">Destination for ${label}</span><select ${available ? "" : "disabled"}>${brainOptions()}</select></label>
      <label class="switch"><input type="checkbox" aria-label="Enable ${label}" ${available ? "" : "disabled"}><span></span></label>`;
    target.append(row);
  });
  const connected = state.data.connections.find((item) => item.provider === "google");
  const connection = $("#google-connection");
  connection.replaceChildren();
  connection.append(
    connected
      ? "Connected · encrypted server-side"
      : available
        ? "Not connected"
        : "OAuth client not configured"
  );
  if (connected) {
    const disconnect = document.createElement("button");
    disconnect.type = "button";
    disconnect.dataset.connectionId = connected.id;
    disconnect.textContent = "Disconnect";
    connection.append(disconnect);
  }
  connection.classList.toggle("connected", Boolean(connected));
  $("#google-form button[type=submit]").disabled = !available;
}

function renderCatalog() {
  const target = $("#integration-grid");
  target.replaceChildren();
  state.data.catalog
    .filter((item) => !item.connector_id.startsWith("google."))
    .slice(0, 6)
    .forEach((item) => {
      const card = document.createElement("article");
      card.className = "catalog-card";
      card.innerHTML = `
        <span class="provider-kicker">${escapeText(item.placement.execution)} · ${escapeText(item.auth.kind)}</span>
        <h3>${escapeText(item.connector_id.replaceAll(".", " / "))}</h3>
        <p>${escapeText(item.source_family)} · shared control contract ready</p>`;
      target.append(card);
    });
}

function renderInstallations() {
  const target = $("#installation-list");
  target.replaceChildren();
  if (!state.data.installations.length) {
    target.innerHTML = '<p class="empty">No live routes yet. Switch on a source above.</p>';
    return;
  }
  const brains = new Map(state.data.brains.map((brain) => [brain.tenant_id, brain.display_name]));
  state.data.installations.forEach((item) => {
    const row = document.createElement("article");
    row.className = "installation";
    const action = item.state === "enabled"
      ? "pause"
      : item.state === "paused"
        ? "resume"
        : item.state === "revoked"
          ? "uninstall"
          : "enable";
    const revoke = item.state === "revoked"
      ? ""
      : `<button data-action="revoke" data-id="${item.id}">revoke</button>`;
    row.innerHTML = `
      <strong>${escapeText(item.connector_id)}</strong>
      <span>${escapeText(brains.get(item.tenant_id) || item.tenant_id)}</span>
      <span class="state">${escapeText(item.state)}</span>
      <div class="installation-actions">
        <button data-action="${action}" data-id="${item.id}">${action}</button>
        ${revoke}
      </div>`;
    target.append(row);
  });
}

function render() {
  renderBrains();
  renderGoogle();
  renderCatalog();
  renderInstallations();
  $(".pulse").classList.add("ready");
  $("#system-label").textContent = "CONTROL PLANE / READY";
}

async function load() {
  try {
    state.data = await api("/admin/api/v1/state");
    render();
  } catch (error) {
    if (error.status === 401) $("#auth-dialog").showModal();
    else toast(`Could not load: ${error.message}`);
  }
}

$("#auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#auth-error").textContent = "";
  try {
    await api("/admin/api/v1/session", {
      method: "POST",
      body: JSON.stringify({ token: $("#admin-token").value }),
    });
    $("#admin-token").value = "";
    $("#auth-dialog").close();
    await load();
  } catch (error) {
    $("#auth-error").textContent = "That key was not accepted.";
  }
});

$("#google-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const routes = [...document.querySelectorAll(".route-row")]
    .filter((row) => row.querySelector("input").checked)
    .map((row) => ({
      connector_id: row.dataset.connector,
      tenant_id: row.querySelector("select").value,
      privacy_mode: "scrub",
      selectors: {},
    }));
  if (!routes.length) return toast("Switch on at least one Google source.");
  try {
    const result = await api("/admin/api/v1/oauth/start", {
      method: "POST",
      body: JSON.stringify({ provider: "google", routes }),
    });
    window.location.assign(result.authorization_url);
  } catch (error) {
    toast(`Authorization did not start: ${error.message}`);
  }
});

$("#installation-list").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  if (
    button.dataset.action === "revoke"
    && !window.confirm("Revoke this routed source? Its checkpoint is retained.")
  ) return;
  try {
    await api(`/admin/api/v1/installations/${button.dataset.id}/actions`, {
      method: "POST",
      body: JSON.stringify({ action: button.dataset.action }),
    });
    toast(`Route ${button.dataset.action}d.`);
    await load();
  } catch (error) {
    toast(`Route unchanged: ${error.message}`);
  }
});

$("#google-connection").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-connection-id]");
  if (!button) return;
  if (!window.confirm("Disconnect Google and revoke every dependent route?")) return;
  try {
    await api(`/admin/api/v1/connections/${button.dataset.connectionId}/revoke`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    toast("Google authority revoked and encrypted credentials wiped.");
    await load();
  } catch (error) {
    toast(`Google remains connected: ${error.message}`);
  }
});

const oauth = new URLSearchParams(window.location.search).get("oauth");
if (oauth === "connected") history.replaceState({}, "", "/admin");
load();
