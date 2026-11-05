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

async function removeService(service) {
  if (!confirm("Stop monitoring '" + service.name + "'?")) return;
  try {
    const response = await fetch("/api/services/" + service.id, { method: "DELETE" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    setMessage("Removed " + service.name, false);
    refresh();
  } catch (err) {
    setMessage("Could not remove the service.", true);
  }
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
    tr.appendChild(cell(s.auto_recovery ? "On" : "Off"));

    const actionTd = document.createElement("td");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "danger";
    btn.textContent = "Remove";
    btn.addEventListener("click", () => removeService(s));
    actionTd.appendChild(btn);
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
    const response = await fetch("/api/services", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ service_name: name }),
    });
    const body = await response.json().catch(() => ({}));
    if (response.ok) {
      input.value = "";
      setMessage("Now monitoring " + name, false);
      refresh();
    } else {
      setMessage(body.message || "Could not add the service.", true);
    }
  } catch (err) {
    setMessage("Could not reach the server.", true);
  }
});

refresh();
setInterval(refresh, REFRESH_MS);
