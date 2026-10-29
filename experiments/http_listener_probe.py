#!/usr/bin/env python3
"""Short-lived HTTP listener probe for a GitHub-hosted Actions runner.

This is a reachability experiment, not a proxy. It serves only a fixed status
payload, binds to 0.0.0.0, self-tests over loopback, and exits after a short hold.
"""
import json
import os
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def get_public_ip():
    endpoints = (
        ("ipify", "https://api.ipify.org"),
        ("aws_checkip", "https://checkip.amazonaws.com"),
        ("ifconfig_me", "https://ifconfig.me/ip"),
    )
    for name, url in endpoints:
        try:
            with urllib.request.urlopen(url, timeout=8) as response:
                value = response.read(128).decode("utf-8", "replace").strip()
                if value:
                    return name, value
        except Exception as exc:
            print(f"[WARN] Public IP lookup via {name} failed: {exc}", flush=True)
    return None, None


def parse_int_env(name, default, minimum, maximum):
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise SystemExit(f"{name} must be between {minimum} and {maximum}")
    return value


PORT = parse_int_env("PROBE_PORT", 8080, 1024, 65535)
HOLD_SECONDS = parse_int_env("HOLD_SECONDS", 300, 30, 600)
STARTED_AT = utc_now()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = {
            "service": "Freeman temporary HTTP listener probe",
            "status": "ok",
            "path": self.path,
            "runner": "GitHub Actions",
            "started_at_utc": STARTED_AT,
            "request_received_at_utc": utc_now(),
            "note": "Diagnostic endpoint only; this is not a proxy.",
        }
        body = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[HTTP] {self.client_address[0]} - {fmt % args}", flush=True)


server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
server.daemon_threads = True
thread = threading.Thread(target=server.serve_forever, name="http-probe", daemon=True)
thread.start()

ip_source, public_ip = get_public_ip()
print("=== HTTP LISTENER PROBE ===", flush=True)
print(f"started_at_utc={STARTED_AT}", flush=True)
print(f"bind_address=0.0.0.0:{PORT}", flush=True)
print(f"hold_seconds={HOLD_SECONDS}", flush=True)
if public_ip:
    print(f"runner_egress_public_ip={public_ip} (source={ip_source})", flush=True)
    print(f"MANUAL_TEST_URL=http://{public_ip}:{PORT}/", flush=True)
    print("IMPORTANT: this URL may be unreachable from the Internet because hosted runners generally do not expose inbound ports.", flush=True)
else:
    print("runner_egress_public_ip=UNKNOWN", flush=True)
print("The listener is running; testing from the runner itself does NOT prove public inbound reachability.", flush=True)

# Prove that the process is listening and serving HTTP locally.
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=5) as response:
        local_body = response.read().decode("utf-8", "replace")
        print(f"LOCAL_SELF_TEST_STATUS={response.status}", flush=True)
        print(f"LOCAL_SELF_TEST_BODY={local_body}", flush=True)
except Exception as exc:
    server.shutdown()
    server.server_close()
    print(f"LOCAL_SELF_TEST_FAILED={exc}", flush=True)
    sys.exit(1)

deadline = time.monotonic() + HOLD_SECONDS
print(f"Listener will remain alive until approximately {utc_now()} + {HOLD_SECONDS}s; use the printed URL during this window.", flush=True)
try:
    while time.monotonic() < deadline:
        time.sleep(min(1, max(0, deadline - time.monotonic())))
finally:
    server.shutdown()
    server.server_close()
    print(f"LISTENER_STOPPED_AT_UTC={utc_now()}", flush=True)
