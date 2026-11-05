"use strict";

const CARD_INTERVAL_MS = 4000;
const CHART_INTERVAL_MS = 10000;

// PROVISIONAL thresholds for coloring only. Phase 7 moves this logic into the
// backend alert engine, where thresholds become configurable.
const THRESHOLDS = {
  cpu: { warn: 70, crit: 85 },
  memory: { warn: 75, crit: 90 },
  disk: { warn: 80, crit: 90 },
};

const $ = (id) => document.getElementById(id);

function stateFor(kind, value) {
  if (value === null || value === undefined) return "wait";
  const t = THRESHOLDS[kind];
  if (value >= t.crit) return "crit";
  if (value >= t.warn) return "warn";
  return "ok";
}

function formatBytes(value) {
  if (value === null || value === undefined) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = Number(value), i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return n.toFixed(i === 0 ? 0 : 1) + " " + units[i];
}

function setCard(cardId, valueId, barId, kind, value) {
  const state = stateFor(kind, value);
  $(cardId).className = "card state-" + state;
  $(valueId).textContent = value === null || value === undefined ? "-" : value.toFixed(1) + "%";
  $(barId).style.width = (value || 0) + "%";
  return state;
}

function setConnection(ok, text) {
  const el = $("conn");
  el.textContent = text;
  el.className = "pill " + (ok ? "pill-ok" : "pill-bad");
}

function setBanner(states) {
  const banner = $("banner"), label = $("banner-text");
  let cls = "wait", text = "WAITING FOR DATA";
  const known = states.filter((s) => s !== "wait");
  if (known.length) {
    if (known.includes("crit")) { cls = "crit"; text = "CRITICAL"; }
    else if (known.includes("warn")) { cls = "warn"; text = "WARNING"; }
    else { cls = "ok"; text = "HEALTHY"; }
  }
  banner.className = "banner banner-" + cls;
  label.textContent = text;
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  const body = await response.json().catch(() => ({}));
  return { status: response.status, body };
}

async function refreshCards() {
  try {
    const { status, body } = await fetchJson("/api/metrics/current");
    if (status === 503) { setConnection(true, "Warming up..."); return; }
    if (status !== 200) throw new Error("HTTP " + status);

    setConnection(true, "Live");
    $("hostname").textContent = "Server: " + (body.hostname || "-");

    const s1 = setCard("card-cpu", "cpu-value", "cpu-bar", "cpu", body.cpu_usage);
    const s2 = setCard("card-memory", "mem-value", "mem-bar", "memory", body.memory_usage);
    const s3 = setCard("card-disk", "disk-value", "disk-bar", "disk", body.disk_usage);
    setBanner([s1, s2, s3]);

    $("net-down").textContent = formatBytes(body.download_bytes_per_sec) + "/s";
    $("net-up").textContent = formatBytes(body.upload_bytes_per_sec) + "/s";
    $("net-sub").textContent =
      "Total: " + formatBytes(body.network_received) + " in / " + formatBytes(body.network_sent) + " out";
    $("uptime").textContent = body.uptime || "-";
    $("system-sub").textContent = "Updated " + new Date(body.timestamp).toLocaleTimeString();
  } catch (err) {
    setConnection(false, "Connection lost");
  }
}

// ----- charts ---------------------------------------------------------------
let currentRange = "15m";
const charts = {};

function makeChart(canvasId, color) {
  return new Chart($(canvasId), {
    type: "line",
    data: { labels: [], datasets: [{
      data: [], borderColor: color, backgroundColor: color + "22",
      fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2,
    }] },
    options: {
      animation: false, responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { min: 0, max: 100, ticks: { color: "#8a99b3" }, grid: { color: "#223049" } },
        x: { ticks: { color: "#8a99b3", maxTicksLimit: 6 }, grid: { display: false } },
      },
    },
  });
}

function timeLabel(iso) {
  const d = new Date(iso);
  return currentRange === "24h" || currentRange === "1h"
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleTimeString();
}

async function refreshCharts() {
  try {
    const { status, body } = await fetchJson("/api/metrics/history?range=" + currentRange);
    if (status !== 200) throw new Error("HTTP " + status);
    const labels = body.points.map((p) => timeLabel(p.timestamp));
    charts.cpu.data.labels = labels;
    charts.cpu.data.datasets[0].data = body.points.map((p) => p.cpu_usage);
    charts.mem.data.labels = labels;
    charts.mem.data.datasets[0].data = body.points.map((p) => p.memory_usage);
    charts.cpu.update(); charts.mem.update();
    $("chart-note").textContent = body.count
      ? body.count + " data points"
      : "No history yet for this range. Data is saved every 10 seconds.";
  } catch (err) {
    $("chart-note").textContent = "Could not load history.";
  }
}

document.querySelectorAll("#range button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#range button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentRange = btn.dataset.range;
    refreshCharts();
  });
});

charts.cpu = makeChart("cpu-chart", "#38bdf8");
charts.mem = makeChart("mem-chart", "#a78bfa");

refreshCards();
refreshCharts();
setInterval(refreshCards, CARD_INTERVAL_MS);
setInterval(refreshCharts, CHART_INTERVAL_MS);
