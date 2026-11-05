"""Memory metrics."""

import psutil


def get_memory_metrics():
    """Return RAM and swap metrics.

    "Used" is defined as total - available. Linux uses spare RAM for caches,
    so counting cache as "used" would make a healthy server look full.
    """
    vm = psutil.virtual_memory()
    used = vm.total - vm.available
    usage_percent = round(used / vm.total * 100, 1) if vm.total else 0.0

    swap = psutil.swap_memory()

    return {
        "total_bytes": vm.total,
        "used_bytes": used,
        "available_bytes": vm.available,
        "usage_percent": usage_percent,
        "swap": {
            "total_bytes": swap.total,
            "used_bytes": swap.used,
            "usage_percent": swap.percent,
        },
    }
