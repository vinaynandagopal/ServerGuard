"use strict";

const CARD_INTERVAL_MS = 4000;
const CHART_INTERVAL_MS = 10000;

const $ = (id) => document.getElementById(id);

// The backend decides the level (NORMAL / WARNING / CRITICAL / UNKNOWN) using
// the thresholds saved on the Settings page. The browser only picks a color.
const LEVEL_STATE = { NORMAL: "ok", WARNING: "warn", CRITICAL: "crit" };
const BANNER_TEXT = { ok: "HEALTHY", warn: "WARNING", crit: "CRITICAL" };

function stateFor(level) {
  return Object.hasOwn(LEVEL_STATE, level) ? LEVEL_STATE[level] : "wait";
}

function formatBytes(value) {
  if (value === null || value === undefined) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = Number(value), i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return n.toFixed(i === 0 ? 0 : 1) + " " + units[i];
}

function setResource(cardId, valueId, barId, subId, level, value, threshold) {
  $(cardId).className = "card state-" + stateFor(level);
  $(valueId).textContent = value === null || value === undefined ? "-" : value.toFixed(1) + "%";
  $(barId).style.width = (value || 0) + "%";
  $(subId).textContent = threshold
    ? "Warning " + threshold.warning + "% / Critical " + threshold.critical + "%"
    : "\u00a0";
}

function setConnection(ok, text) {
  const el = $("conn");
  el.textContent = text;
  el.className = "pill " + (ok ? "pill-ok" : "pill-bad");
}

function setBanner(status) {
  const state = stateFor(status);
  $("banner").className = "banner banner-" + state;
  $("banner-text").textContent = BANNER_TEXT[state] || "WAITING FOR DATA";
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

    setResource("card-cpu", "cpu-value", "cpu-bar", "cpu-sub",
      body.levels.cpu, body.cpu_usage, body.thresholds.cpu);
    setResource("card-memory", "mem-value", "mem-bar", "mem-sub",
      body.levels.memory, body.memory_usage, body.thresholds.memory);
    setResource("card-disk", "disk-value", "disk-bar", "disk-sub",
      body.levels.disk, body.disk_usage, body.thresholds.disk);
    setBanner(body.status);

    $("net-down").textContent = formatBytes(body.download_bytes_per_sec) + "/s";
    $("net-up").textContent = formatBytes(body.upload_bytes_per_sec) + "/s";
    $("net-sub").textContent =
      "Total: " + formatBytes(body.network_received) + " in / " + formatBytes(body.network_sent) + " out";
    $("uptime").textContent = body.uptime || "-";
    $("system-sub").textContent = "Updated " + new Date(body.timestamp).toLocaleTimeString();

    $("alerts-value").textContent = body.active_alerts;
    $("card-alerts").className = "card state-" + (body.active_alerts > 0 ? "warn" : "ok");
    $("svc-running").textContent = body.services_running;
    $("svc-stopped").textContent = body.services_stopped;
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
