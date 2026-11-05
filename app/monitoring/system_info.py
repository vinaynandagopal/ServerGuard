"""General system information: hostname, distro, kernel, uptime."""

import platform
import time
from datetime import datetime, timezone

import psutil


def format_uptime(seconds):
    """Turn seconds into text like '2 days, 4 hours, 5 minutes'."""
    seconds = max(0, int(seconds))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60

    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes or not parts:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return ", ".join(parts)


def _distribution_name():
    try:
        info = platform.freedesktop_os_release()  # reads /etc/os-release
        return info.get("PRETTY_NAME") or info.get("NAME") or "Linux"
    except (OSError, AttributeError):
        return platform.system() or "Unknown"


def get_system_info():
    """Return static and slowly changing system facts."""
    try:
        boot_timestamp = psutil.boot_time()
        uptime_seconds = max(0, int(time.time() - boot_timestamp))
        boot_time_utc = datetime.fromtimestamp(
            boot_timestamp, tz=timezone.utc
        ).isoformat()
    except (OSError, AttributeError):
        uptime_seconds = None
        boot_time_utc = None

    return {
        "hostname": platform.node(),
        "distribution": _distribution_name(),
        "kernel": platform.release(),
        "boot_time_utc": boot_time_utc,
        "uptime_seconds": uptime_seconds,
        "uptime_human": (
            format_uptime(uptime_seconds) if uptime_seconds is not None else None
        ),
    }
