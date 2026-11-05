"use strict";

const $ = (id) => document.getElementById(id);
const KEYS = [
  "cpu_warning", "cpu_critical",
  "memory_warning", "memory_critical",
  "disk_warning", "disk_critical",
  "monitor_interval",
];
let defaults = {};

function setMessage(text, isError) {
  const el = $("settings-msg");
  el.textContent = text;
  el.className = "small " + (isError ? "msg-error" : "msg-ok");
}

function fill(values) {
  for (const key of KEYS) $(key).value = values[key];
}

async function load() {
  try {
    const response = await fetch("/api/settings", { cache: "no-store" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    defaults = data.defaults;
    fill(data.settings);
    const pill = $("conn");
    pill.textContent = "Loaded";
    pill.className = "pill pill-ok";
  } catch (err) {
    const pill = $("conn");
    pill.textContent = "Connection lost";
    pill.className = "pill pill-bad";
  }
}

$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {};
  for (const key of KEYS) {
    const raw = $(key).value.trim();
    const number = Number(raw);
    if (raw === "" || !Number.isFinite(number)) {
      setMessage("Enter a number for every field.", true);
      return;
    }
    payload[key] = number;
  }
  try {
    const response = await fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await response.json().catch(() => ({}));
    if (response.ok) {
      fill(body.settings);
      setMessage("Saved. New thresholds apply on the next check.", false);
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
