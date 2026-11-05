"use strict";

const REFRESH_MS = 4000;
const $ = (id) => document.getElementById(id);
let requestId = 0;

function setConnection(ok, text) {
  const el = $("conn");
  el.textContent = text;
  el.className = "pill " + (ok ? "pill-ok" : "pill-bad");
}

function cell(text, className) {
  const td = document.createElement("td");
  td.textContent = text;
  if (className) td.className = className;
  return td;
}

function renderRows(processes) {
  const body = $("proc-body");
  body.replaceChildren();
  if (!processes.length) {
    const tr = document.createElement("tr");
    const td = cell("No processes match.", "empty");
    td.colSpan = 6;
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  for (const p of processes) {
    const tr = document.createElement("tr");
    tr.appendChild(cell(String(p.pid), "mono"));
    tr.appendChild(cell(p.name, "mono"));
    tr.appendChild(cell(p.username));
    tr.appendChild(cell(p.cpu_percent.toFixed(1), "num"));
    tr.appendChild(cell(p.memory_percent.toFixed(1), "num"));
    tr.appendChild(cell(p.status));
    body.appendChild(tr);
  }
}

async function refresh() {
  const mine = ++requestId;
  const params = new URLSearchParams({
    sort: $("sort").value,
    order: $("order").value,
    limit: $("limit").value,
    search: $("search").value.trim(),
  });
  try {
    const response = await fetch("/api/processes?" + params.toString(), { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (mine !== requestId) return;  // a newer request replaced this one

    if (response.status === 503) { setConnection(true, "Warming up..."); return; }
    if (!response.ok) throw new Error(data.message || "HTTP " + response.status);

    setConnection(true, "Live");
    renderRows(data.processes);
    $("proc-note").textContent =
      "Showing " + data.returned + " of " + data.matched + " matching (" +
      data.total_processes + " processes total)";
    $("proc-updated").textContent = "Updated " + new Date(data.updated).toLocaleTimeString();
  } catch (err) {
    if (mine === requestId) setConnection(false, "Connection lost");
  }
}

let searchTimer = null;
$("search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(refresh, 300);  // wait until typing pauses
});
for (const id of ["sort", "order", "limit"]) {
  $(id).addEventListener("change", refresh);
}

refresh();
setInterval(refresh, REFRESH_MS);
