"use strict";

const REFRESH_MS = 5000;
const $ = (id) => document.getElementById(id);

// status from the API -> [label, badge color]
const STATUS_VIEW = {
  "active": ["Running", "ok"],
  "inactive": ["Stopped", "crit"],
  "failed": ["Failed", "crit"],
  "activating": ["Starting", "warn"],
  "deactivating": ["Stopping", "warn"],
  "reloading": ["Reloading", "warn"],
  "not-installed": ["Not installed", "muted"],
  "unavailable": ["systemd unavailable", "muted"],
  "unknown": ["Unknown", "muted"],
  "pending": ["Checking...", "muted"],
};

function fmtTime(iso) {
  return iso ? new Date(iso).toLocaleString() : "-";
}

function cell(text, className) {
  const td = document.createElement("td");
  td.textContent = text;
  if (className) td.className = className;
  return td;
}

function setConnection(ok, text) {
  const el = $("conn");
  el.textContent = text;
  el.className = "pill " + (ok ? "pill-ok" : "pill-bad");
}

function setMessage(text, isError) {
  const el = $("form-msg");
  el.textContent = text;
  el.className = "small " + (isError ? "msg-error" : "msg-ok");
}

// The X-ServerGuard header blocks other websites from calling state-changing endpoints.
async function api(method, url, body) {
  const headers = { "X-ServerGuard": "1" };
  const options = { method, headers };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, data };
}

async function removeService(service) {
  if (!confirm("Stop monitoring '" + service.name + "'?")) return;
  try {
    const r = await api("DELETE", "/api/services/" + service.id);
    if (!r.ok) throw new Error("HTTP " + r.status);
    setMessage("Removed " + service.name, false);
    refresh();
  } catch (err) {
    setMessage("Could not remove the service.", true);
  }
}

async function restartService(service) {
  if (!confirm("Restart '" + service.name + "' now?")) return;
  setMessage("Restarting " + service.name + "...", false);
  try {
    const r = await api("POST", "/api/services/" + service.id + "/restart");
    if (r.ok) setMessage(service.name + " restarted and verified running.", false);
    else setMessage(r.data.message || "Restart failed.", true);
    refresh();
  } catch (err) {
    setMessage("Could not reach the server.", true);
  }
}

async function toggleRecovery(service) {
  try {
    const r = await api("PUT", "/api/services/" + service.id + "/recovery",
      { enabled: !service.auto_recovery });
    if (!r.ok) throw new Error(r.data.message || "HTTP " + r.status);
    setMessage("Automatic recovery for " + service.name + " is now " +
      (!service.auto_recovery ? "on." : "off."), false);
    refresh();
  } catch (err) {
    setMessage(err.message, true);
  }
}

function button(label, className, handler) {
  const btn = document.createElement("button");
  btn.type = "button";
  if (className) btn.className = className;
  btn.textContent = label;
  btn.addEventListener("click", handler);
  return btn;
}

function renderRows(services) {
  const body = $("services-body");
  body.replaceChildren();

  if (!services.length) {
    const tr = document.createElement("tr");
    const td = cell("No services are monitored yet. Add one above.", "empty");
    td.colSpan = 6;
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }

  for (const s of services) {
    const tr = document.createElement("tr");
    tr.appendChild(cell(s.name, "mono"));

    const [label, kind] = Object.hasOwn(STATUS_VIEW, s.status)
      ? STATUS_VIEW[s.status] : [s.status, "muted"];
    const statusTd = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "badge badge-" + kind;
    badge.textContent = label;
    statusTd.appendChild(badge);
    tr.appendChild(statusTd);

    tr.appendChild(cell(fmtTime(s.last_status_change)));
    tr.appendChild(cell(String(s.failure_count), "num"));

    const recoveryTd = document.createElement("td");
    if (s.allowlisted) {
      recoveryTd.appendChild(button(
        s.auto_recovery ? "On" : "Off",
        s.auto_recovery ? "toggle-on" : "",
        () => toggleRecovery(s),
      ));
    } else {
      recoveryTd.textContent = "Not allowlisted";
      recoveryTd.className = "muted";
    }
    tr.appendChild(recoveryTd);

    const actionTd = document.createElement("td");
    const row = document.createElement("div");
    row.className = "row-actions";
    if (s.allowlisted) row.appendChild(button("Restart", "", () => restartService(s)));
    row.appendChild(button("Remove", "danger", () => removeService(s)));
    actionTd.appendChild(row);
    tr.appendChild(actionTd);

    body.appendChild(tr);
  }
}

async function refresh() {
  try {
    const response = await fetch("/api/services", { cache: "no-store" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    setConnection(true, "Live");
    $("systemd-warning").hidden = data.systemd_available;
    $("sum-running").textContent = data.summary.running;
    $("sum-stopped").textContent = data.summary.stopped;
    $("sum-missing").textContent = data.summary.not_installed;
    renderRows(data.services);
  } catch (err) {
    setConnection(false, "Connection lost");
  }
}

$("add-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("add-name");
  const name = input.value.trim();
  if (!name) return;
  try {
    const r = await api("POST", "/api/services", { service_name: name });
    if (r.ok) {
      input.value = "";
      setMessage("Now monitoring " + name, false);
      refresh();
    } else {
      setMessage(r.data.message || "Could not add the service.", true);
    }
  } catch (err) {
    setMessage("Could not reach the server.", true);
  }
});

refresh();
setInterval(refresh, REFRESH_MS);
