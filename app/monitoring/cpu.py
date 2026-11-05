"""CPU metrics."""

import os

import psutil


def get_cpu_metrics():
    """Return current CPU metrics.

    psutil.cpu_percent(interval=None) is non-blocking: it reports usage since
    the previous call. The very first call in a process returns a meaningless
    0.0, which is why the collector "primes" it before reading real values.
    """
    usage = psutil.cpu_percent(interval=None)

    try:
        load_1, load_5, load_15 = os.getloadavg()
    except (OSError, AttributeError):
        load_1 = load_5 = load_15 = None

    return {
        "usage_percent": usage,
        "cores_logical": psutil.cpu_count(logical=True),
        "cores_physical": psutil.cpu_count(logical=False),  # may be None in a VM
        "load_avg": {"1m": load_1, "5m": load_5, "15m": load_15},
    }
