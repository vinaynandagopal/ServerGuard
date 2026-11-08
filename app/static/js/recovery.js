"use strict";

const REFRESH_MS = 5000;
const $ = (id) => document.getElementById(id);

const RESULT_VIEW = { SUCCESS: ["Success", "ok"], FAILURE: ["Failed", "crit"] };
const ACTION_LABEL = { AUTO_RESTART: "Automatic", MANUAL_RESTART: "Manual" };

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

function renderStatus(s) {
  $("rs-enabled").textContent = s.recovery_enabled ? "On" : "Off";
  $("rs-helper").textContent = s.script_installed ? "Installed" : "Not installed";
  $("rs-allowlist").textContent = s.allowlist.length ? s.allowlist.join(", ") : "none";
  $("rs-limits").textContent =
    "Up to " + s.max_restart_attempts + " automatic restarts per service per hour, at least " +
    s.cooldown_seconds + " s apart";
}

function renderRows(logs) {
  const body = $("recovery-body");
  body.replaceChildren();
  if (!logs.length) {
    const tr = document.createElement("tr");
    const td = cell("No recovery attempts yet.", "empty");
    td.colSpan = 5;
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  for (const log of logs) {
    const tr = document.createElement("tr");
    tr.appendChild(cell(new Date(log.timestamp).toLocaleString()));
    tr.appendChild(cell(log.service_name, "mono"));
    tr.appendChild(cell(Object.hasOwn(ACTION_LABEL, log.action) ? ACTION_LABEL[log.action] : log.action));

    const [label, kind] = Object.hasOwn(RESULT_VIEW, log.result) ? RESULT_VIEW[log.result] : [log.result, "muted"];
    const td = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "badge badge-" + kind;
    badge.textContent = label;
    td.appendChild(badge);
    tr.appendChild(td);

    tr.appendChild(cell(log.message || ""));
    body.appendChild(tr);
  }
}

async function refresh() {
  try {
    const response = await fetch("/api/recovery/history?limit=200", { cache: "no-store" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    setConnection(true, "Live");
    $("sum-total").textContent = data.summary.total;
    $("sum-success").textContent = data.summary.success;
    $("sum-failure").textContent = data.summary.failure;
    renderStatus(data.status);
    renderRows(data.logs);
  } catch (err) {
    setConnection(false, "Connection lost");
  }
}

refresh();
setInterval(refresh, REFRESH_MS);
