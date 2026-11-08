"use strict";

const $ = (id) => document.getElementById(id);
const NUMBER_KEYS = [
  "cpu_warning", "cpu_critical",
  "memory_warning", "memory_critical",
  "disk_warning", "disk_critical",
  "monitor_interval",
  "max_restart_attempts", "cooldown_seconds",
];
const BOOL_KEYS = ["recovery_enabled"];
let defaults = {};

function setMessage(text, isError) {
  const el = $("settings-msg");
  el.textContent = text;
  el.className = "small " + (isError ? "msg-error" : "msg-ok");
}

function fill(values) {
  for (const key of NUMBER_KEYS) $(key).value = values[key];
  for (const key of BOOL_KEYS) $(key).checked = Boolean(values[key]);
}

function setPill(ok, text) {
  const pill = $("conn");
  pill.textContent = text;
  pill.className = "pill " + (ok ? "pill-ok" : "pill-bad");
}

async function load() {
  try {
    const response = await fetch("/api/settings", { cache: "no-store" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    defaults = data.defaults;
    fill(data.settings);
    setPill(true, "Loaded");
  } catch (err) {
    setPill(false, "Connection lost");
  }
}

$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {};
  for (const key of NUMBER_KEYS) {
    const raw = $(key).value.trim();
    const number = Number(raw);
    if (raw === "" || !Number.isFinite(number)) {
      setMessage("Enter a number for every field.", true);
      return;
    }
    payload[key] = number;
  }
  for (const key of BOOL_KEYS) payload[key] = $(key).checked;

  try {
    const response = await fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await response.json().catch(() => ({}));
    if (response.ok) {
      fill(body.settings);
      setMessage("Saved. New settings apply on the next check.", false);
    } else {
      setMessage(body.message || "Could not save the settings.", true);
    }
  } catch (err) {
    setMessage("Could not reach the server.", true);
  }
});

$("reset-btn").addEventListener("click", () => {
  fill(defaults);
  setMessage("Defaults loaded. Click Save to apply them.", false);
});

load();
