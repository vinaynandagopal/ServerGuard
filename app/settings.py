"""Alert thresholds, recovery limits and monitoring interval, stored in SQLite."""

import json
import math

from .database import db

DEFAULTS = {
    "cpu_warning": 70.0,
    "cpu_critical": 85.0,
    "memory_warning": 75.0,
    "memory_critical": 90.0,
    "disk_warning": 80.0,
    "disk_critical": 90.0,
    "monitor_interval": 5.0,
    "recovery_enabled": False,       # global master switch, off by default
    "max_restart_attempts": 2.0,     # automatic restarts per service per hour
    "cooldown_seconds": 300.0,       # minimum gap between automatic restarts
}
RESOURCES = ("cpu", "memory", "disk")
BOOL_KEYS = ("recovery_enabled",)
THRESHOLD_MAX = 100
INTERVAL_MIN, INTERVAL_MAX = 2, 60
ATTEMPTS_MIN, ATTEMPTS_MAX = 1, 10
COOLDOWN_MIN, COOLDOWN_MAX = 10, 3600


def defaults_for(config):
    """Defaults, with the interval taken from the app config / environment."""
    result = dict(DEFAULTS)
    result["monitor_interval"] = float(config["MONITOR_INTERVAL"])
    return result


def _is_number(value):
    # bool is a subclass of int, so it must be excluded explicitly.
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _check_value(key, value):
    """Return an error message, or None if the numeric value is acceptable."""
    if key == "monitor_interval":
        if not INTERVAL_MIN <= value <= INTERVAL_MAX:
            return f"Must be between {INTERVAL_MIN} and {INTERVAL_MAX} seconds."
    elif key == "max_restart_attempts":
        if value != int(value) or not ATTEMPTS_MIN <= value <= ATTEMPTS_MAX:
            return f"Must be a whole number from {ATTEMPTS_MIN} to {ATTEMPTS_MAX}."
    elif key == "cooldown_seconds":
        if not COOLDOWN_MIN <= value <= COOLDOWN_MAX:
            return f"Must be between {COOLDOWN_MIN} and {COOLDOWN_MAX} seconds."
    elif not 0 < value <= THRESHOLD_MAX:
        return f"Must be greater than 0 and at most {THRESHOLD_MAX}."
    return None


def load(db_path, defaults=None):
    """Saved settings merged over the defaults. Corrupt values are ignored."""
    result = dict(defaults or DEFAULTS)
    for key, raw in db.get_all_settings(db_path).items():
        if key not in result:
            continue
        try:
            value = json.loads(raw)
        except ValueError:
            continue
        if key in BOOL_KEYS:
            if isinstance(value, bool):
                result[key] = value
        elif _is_number(value) and _check_value(key, value) is None:
            result[key] = float(value)

    # A warning level at or above its critical level makes no sense; fall back.
    base = defaults or DEFAULTS
    for name in RESOURCES:
        warn_key, crit_key = f"{name}_warning", f"{name}_critical"
        if result[warn_key] >= result[crit_key]:
            result[warn_key], result[crit_key] = base[warn_key], base[crit_key]
    return result


def validate_update(current, updates):
    """Return (merged_settings, {}) if valid, or (None, {field: message})."""
    if not isinstance(updates, dict) or not updates:
        return None, {"_": "Send a JSON object with at least one setting."}

    errors = {}
    merged = dict(current)
    for key, value in updates.items():
        if key not in DEFAULTS:
            errors[key] = "Unknown setting."
        elif key in BOOL_KEYS:
            if isinstance(value, bool):
                merged[key] = value
            else:
                errors[key] = "Must be true or false."
        elif not _is_number(value):
            errors[key] = "Must be a number."
        else:
            problem = _check_value(key, value)
            if problem:
                errors[key] = problem
            else:
                merged[key] = float(value)

    for name in RESOURCES:
        warn_key, crit_key = f"{name}_warning", f"{name}_critical"
        if warn_key in errors or crit_key in errors:
            continue
        if merged[warn_key] >= merged[crit_key]:
            errors[warn_key] = "Warning must be lower than critical."

    return (None, errors) if errors else (merged, {})


def save(db_path, merged, keys):
    db.save_settings(db_path, {k: json.dumps(merged[k]) for k in keys})
