# E3 — Microsoft Edge browser proxy through GitHub Actions

## Verification results (2026-10-29)

All five pass criteria were verified end-to-end (runs 40840121168, 40843329649):

| Criterion | Result | Evidence |
|---|---|---|
| Tunnel | PASS | Quick Tunnel URL logged; `/health` reachable |
| Authenticated transport | PASS | Local SOCKS5 client completed WSS handshake with Bearer token |
| Browser proxy | PASS | Dedicated Edge instance opened HTTPS pages via `socks5://127.0.0.1:1080` |
| Egress evidence | PASS | `api.ipify.org` through the tunnel matched `E3_RUNNER_EGRESS_IP` exactly (134.33.76.12, then 172.202.119.179) |
| Bounded lifetime | PASS | Each run self-terminated after `hold_seconds`; the tunnel returned HTTP 530 once the hold expired |

Operational notes from the runs:

- `workflow_dispatch` cannot reach this workflow while it lives only on a non-default branch; a branch-scoped `push` trigger was used to run it pre-merge and is left disabled in the committed file.
- In-progress job logs via the REST API were unreliable (intermittent `BlobNotFound`), so the tunnel URL was read from the Actions console.
- One verification window was missed because the 300-second hold expired before the local client connected; treat the URL as having a hard deadline.

## Goal

Test whether a Windows Edge instance can use a local SOCKS5 endpoint whose TCP streams are relayed over an authenticated WebSocket/Cloudflare Quick Tunnel to a GitHub-hosted runner, so destination HTTP/HTTPS connections originate from the runner.

This is a short-lived technical experiment, not a supported or persistent VPN/proxy service. The Quick Tunnel URL changes between runs, the hosted runner is ephemeral, and third-party tunnel availability/policies apply.

## Architecture

```text
Microsoft Edge (dedicated profile)
  -> SOCKS5 127.0.0.1:1080 (Windows)
  -> authenticated WebSocket over HTTPS/WSS
  -> Cloudflare Quick Tunnel URL
  -> GitHub Actions runner (127.0.0.1:8765)
  -> outbound TCP destination (only ports 80/443)
```

The relay rejects non-public destination IPs after DNS resolution and permits only TCP ports 80 and 443. The local SOCKS5 server binds only to loopback. This is still experimental code: do not use it for sensitive accounts or confidential traffic.

## 1. Create a temporary token

On Windows, with Python installed, run:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Copy the generated value into the repository's **Settings → Secrets and variables → Actions → New repository secret** as `E3_TUNNEL_TOKEN`.

Do not commit the token, put it in workflow inputs, or post it in an issue/PR. The same token is needed in the local PowerShell terminal later. After testing, delete or rotate the repository secret.

## 2. Start the GitHub Actions experiment

1. Open **Actions → Freeman: E3 Edge browser proxy tunnel**.
2. Choose **Run workflow** on branch `experiment/e3-edge-browser-proxy`.
3. Choose `hold_seconds` between 60 and 900; start with 300.
4. Keep the workflow run active while testing locally.
5. In the logs, copy `E3_TUNNEL_URL=https://....trycloudflare.com` and note `E3_RUNNER_EGRESS_IP=...`.

The job intentionally fails early if `E3_TUNNEL_TOKEN` is not configured. The status endpoint is unauthenticated but returns only a fixed health status. The WebSocket relay itself requires the token.

## 3. Start the local SOCKS5 client

In a Windows PowerShell terminal, from a local clone of this repository, install the pinned dependency and set the environment variable:

```powershell
python --version  # use Python 3.11 or newer
python -m pip install -r experiments/e3-requirements.txt
Read-Host 'Enter E3 token' -AsSecureString | ForEach-Object {
  $env:E3_TUNNEL_TOKEN = [System.Net.NetworkCredential]::new('', $_).Password
}
python experiments/e3_edge_socks_client.py 'https://YOUR-RUN.trycloudflare.com'
```

Replace the URL with the one printed by the active workflow. Keep this terminal open. The local service listens only on `127.0.0.1:1080`.

Avoid entering the token into a command-line argument, committing it to a file, or sharing terminal output containing it. Close the terminal with Ctrl+C when done.

## 4. Launch a separate Edge instance

Open a second PowerShell terminal and run:

```powershell
$edge = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
if (-not (Test-Path $edge)) {
  $edge = "${env:ProgramFiles}\Microsoft\Edge\Application\msedge.exe"
}
$userData = Join-Path $env:TEMP 'Edge-E3-Proxy-Profile'
Start-Process -FilePath $edge -ArgumentList @(
  '--proxy-server=socks5://127.0.0.1:1080',
  "--user-data-dir=$userData",
  '--no-first-run',
  'https://api.ipify.org'
)
```

This creates a separate temporary Edge profile and does not intentionally change the Windows system proxy. Do not sign in to personal accounts in this experimental profile.

The IP displayed by `https://api.ipify.org` should match `E3_RUNNER_EGRESS_IP` from the same workflow run. Repeat with `https://example.com` to check ordinary HTTPS navigation. DNS, WebSocket, Cloudflare, GitHub Actions, or destination policy may still affect the result.

## 5. Pass/fail criteria

- **PASS — tunnel:** workflow logs a Quick Tunnel URL and the public `/health` endpoint returns `{"service":"Freeman E3 relay","status":"ok"}`.
- **PASS — authenticated transport:** the local SOCKS5 client connects to the runner relay using the token.
- **PASS — browser proxy:** Edge opens an HTTPS page through the SOCKS5 endpoint.
- **PASS — egress evidence:** the public IP shown through Edge matches the runner's direct egress IP for that run.
- **FAIL/INCONCLUSIVE:** 401/403/502, WebSocket upgrade failure, timeout, missing URL, or an IP mismatch. Capture the workflow logs and the local client's error type, but never share the token.

A successful `/health` response alone does **not** prove that proxy traffic works.

## 6. Stop and clean up

1. Close the dedicated Edge window.
2. Press Ctrl+C in the local SOCKS5 client terminal.
3. Cancel the workflow run if you want to stop early; otherwise it exits after the configured hold time.
4. Delete or rotate `E3_TUNNEL_TOKEN` in repository Actions secrets.
5. Optionally delete the temporary Edge profile at `$env:TEMP\Edge-E3-Proxy-Profile` after Edge has closed.

## Limitations and security notes

- This transports TCP only and allows destination ports 80/443; it does not provide UDP, QUIC, arbitrary ports, or system-wide routing.
- Edge may fall back to direct connections for some traffic or browser services; IP comparison is the practical check for the test page.
- The runner and Quick Tunnel are temporary and their outbound IP can change between runs.
- Do not use this experiment for banking, sensitive logins, or confidential information. A proxy can observe destination metadata, and the code has not received a production security audit.
- Follow GitHub Actions and Cloudflare acceptable-use policies. Do not use this to evade access controls or abuse destination services.
