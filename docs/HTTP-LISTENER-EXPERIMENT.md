# HTTP Listener Reachability Experiment

## Goal

Determine separately whether a GitHub-hosted Actions runner can:
1. bind and serve HTTP on a port;
2. print its observed egress public IP and a candidate `http://IP:PORT/` URL;
3. accept a connection initiated from the user's local computer.

## Run it

1. Open the repository's Actions tab.
2. Select **Freeman: HTTP listener reachability probe** and click **Run workflow**.
3. Use port `8080` and hold time `300` seconds for the first run.
4. Open the running job log and find `MANUAL_TEST_URL=`.
5. While the job is still running, try the URL in a browser from your own computer.
6. Record the result below. Do not infer public reachability from `LOCAL_SELF_TEST_STATUS=200`; that only proves the service works inside the runner.

## Important network distinction

The printed public IP is the runner's observed outbound/NAT address. It is not necessarily an address assigned directly to the runner, and an egress address does not imply that unsolicited inbound connections can reach the job. GitHub-hosted runner network policy, NAT, and firewalling generally mean direct `IP:PORT` access is not available. The experiment intentionally does not open a reverse tunnel or expose a proxy.

If direct access fails but the local self-test succeeds, the next experiment should use an explicitly configured inbound path (for example, a controlled test VPS with a reverse tunnel or a temporary authenticated tunnel service), rather than repeatedly changing the HTTP server.

## Safety limits

- Fixed diagnostic JSON response only; no arbitrary URL fetching or forwarding.
- No secrets or credentials are required.
- Port is restricted to 1024-65535.
- Runtime is limited to 30-600 seconds.
- Listener is closed when the job finishes.

## Results

| Run date (UTC) | Runner egress IP | Port | Local self-test | Local computer access | Notes |
|---|---|---:|---|---|---|
| | | 8080 | | | |
