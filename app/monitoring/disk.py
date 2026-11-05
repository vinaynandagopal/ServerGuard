"""Disk metrics."""

import psutil


def _usage_dict(mountpoint, usage):
    return {
        "mountpoint": mountpoint,
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "usage_percent": usage.percent,
    }


def get_disk_metrics(path="/"):
    """Return usage of the primary path plus all readable mounted partitions.

    A partition we cannot read (permissions, stale mount) is skipped instead of
    crashing the whole collection cycle.
    """
    primary = None
    try:
        primary = _usage_dict(path, psutil.disk_usage(path))
    except OSError:
        primary = None

    partitions = []
    seen = set()
    try:
        found = psutil.disk_partitions(all=False)
    except OSError:
        found = []

    for part in found:
        if part.mountpoint in seen:
            continue
        seen.add(part.mountpoint)
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:  # PermissionError is a subclass of OSError
            continue
        entry = _usage_dict(part.mountpoint, usage)
        entry["device"] = part.device
        entry["fstype"] = part.fstype
        partitions.append(entry)

    return {"primary": primary, "partitions": partitions}
