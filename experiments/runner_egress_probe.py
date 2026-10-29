#!/usr/bin/env python3
"""Safely inspect the GitHub-hosted runner's outbound public IP.

Uses only the Python standard library. This is a diagnostic probe, not a proxy.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import Request, urlopen

SERVICES = {
    "ipify": "https://api.ipify.org",
    "aws_checkip": "https://checkip.amazonaws.com",
    "ifconfig_me": "https://ifconfig.me/ip",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_ip(url: str) -> dict[str, str]:
    request = Request(url, headers={"User-Agent": "Freeman-Egress-Probe/1.0"})
    try:
        with urlopen(request, timeout=10) as response:
            value = response.read(256).decode("utf-8", errors="replace").strip()
            return {"status": "ok", "value": value, "http_status": str(response.status)}
    except (OSError, URLError, TimeoutError) as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def parse_hold_seconds() -> int:
    raw = os.environ.get("HOLD_SECONDS", "0").strip()
    try:
        seconds = int(raw)
    except ValueError as exc:
        raise SystemExit("HOLD_SECONDS must be an integer from 0 to 600") from exc
    if not 0 <= seconds <= 600:
        raise SystemExit("HOLD_SECONDS must be an integer from 0 to 600")
    return seconds


def main() -> int:
    hold_seconds = parse_hold_seconds()
    report: dict[str, object] = {
        "experiment": "E1-runner-egress-ip",
        "started_at_utc": utc_now(),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "runner_os": os.environ.get("RUNNER_OS", "unknown"),
        "runner_arch": os.environ.get("RUNNER_ARCH", "unknown"),
        "github_actions": os.environ.get("GITHUB_ACTIONS", "false"),
        "ip_services": {},
    }

    results: dict[str, dict[str, str]] = {}
    for name, url in SERVICES.items():
        print(f"\n--- Querying {name} ---", flush=True)
        result = fetch_ip(url)
        results[name] = result
        print(json.dumps({name: result}, ensure_ascii=False), flush=True)
    report["ip_services"] = results

    observed = {
        result.get("value")
        for result in results.values()
        if result.get("status") == "ok" and result.get("value")
    }
    report["distinct_successful_ip_values"] = sorted(observed)
    report["successful_service_count"] = sum(
        1 for result in results.values() if result.get("status") == "ok"
    )
    report["completed_probe_at_utc"] = utc_now()

    print("\n=== EGRESS PROBE SUMMARY ===", flush=True)
    print(json.dumps(report, indent=2, ensure_ascii=False), flush=True)

    if hold_seconds:
        print(f"\nHolding runner for {hold_seconds} seconds (requested short-duration test).", flush=True)
        time.sleep(hold_seconds)

    print(f"\nFinished at UTC: {utc_now()}", flush=True)
    return 0 if report["successful_service_count"] else 1


if __name__ == "__main__":
    sys.exit(main())
