"use strict";

const REFRESH_MS = 5000;
const $ = (id) => document.getElementById(id);

const SEVERITY_VIEW = { WARNING: ["Warning", "warn"], CRITICAL: ["Critical", "crit"] };
const STATUS_VIEW = {
  ACTIVE: ["Active", "warn"],
  ACKNOWLEDGED: ["Acknowledged", "muted"],
  RESOLVED: ["Resolved", "ok"],
};
const METRIC_LABEL = { cpu: "CPU", memory: "Memory", disk: "Disk" };

function metricLabel(metric) {
  if (Object.hasOwn(METRIC_LABEL, metric)) return METRIC_LABEL[metric];
  if (metric.startsWith("service:")) return "Service: " + metric.slice(8);
  return metric;
}

function fmtTime(iso) { return iso ? new Date(iso).toLocaleString() : "-"; }

function cell(text, className) {
  const td = document.createElement("td");
  td.textContent = text;
  if (className) td.className = className;
  return td;
}

function badgeCell(view, key) {
  const [label, kind] = Object.hasOwn(view, key) ? view[key] : [key, "muted"];
  const td = document.createElement("td");
  const badge = document.createElement("span");
  badge.className = "badge badge-" + kind;
  badge.textContent = label;
  td.appendChild(badge);
  return td;
}

function setConnection(ok, text) {
  const el = $("conn");
  el.textContent = text;
  el.className = "pill " + (ok ? "pill-ok" : "pill-bad");
}

function setMessage(text, isError) {
  const el = $("alert-msg");
  el.textContent = text;
  el.className = "small " + (isError ? "msg-error" : "msg-ok");
}

async function acknowledge(alert) {
  try {
    const response = await fetch("/api/alerts/" + alert.id + "/acknowledge", { method: "POST" });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.message || "HTTP " + response.status);
    setMessage("Alert acknowledged.", false);
    refresh();
  } catch (err) {
    setMessage(err.message, true);
  }
}

function renderRows(alerts) {
  const body = $("alerts-body");
  body.replaceChildren();
  if (!alerts.length) {
    const tr = document.createElement("tr");
    const td = cell("No alerts match this filter.", "empty");
    td.colSpan = 7;
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  for (const a of alerts) {
    const tr = document.createElement("tr");
    tr.appendChild(cell(fmtTime(a.timestamp)));
    tr.appendChild(badgeCell(SEVERITY_VIEW, a.severity));
    tr.appendChild(cell(metricLabel(a.metric)));
    tr.appendChild(cell(a.message));
    tr.appendChild(badgeCell(STATUS_VIEW, a.status));
    tr.appendChild(cell(fmtTime(a.resolved_at)));

    const actionTd = document.createElement("td");
    if (a.status === "ACTIVE") {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = "Acknowledge";
      btn.addEventListener("click", () => acknowledge(a));
      actionTd.appendChild(btn);
    }
    tr.appendChild(actionTd);
    body.appendChild(tr);
  }
}

async function refresh() {
  const params = new URLSearchParams({
    status: $("status-filter").value,
    severity: $("severity-filter").value,
    limit: "200",
  });
  try {
    const response = await fetch("/api/alerts?" + params.toString(), { cache: "no-store" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    setConnection(true, "Live");
    $("sum-total").textContent = data.summary.total;
    $("sum-critical").textContent = data.summary.critical;
    $("sum-active").textContent = data.summary.active;
    $("sum-resolved").textContent = data.summary.resolved;
    renderRows(data.alerts);
  } catch (err) {
    setConnection(false, "Connection lost");
  }
}

$("status-filter").addEventListener("change", refresh);
$("severity-filter").addEventListener("change", refresh);
refresh();
setInterval(refresh, REFRESH_MS);
