#!/usr/bin/env python3
"""Temporary, authenticated WebSocket-to-TCP relay for the E3 experiment.

Only public destinations on TCP ports 80 and 443 are allowed. This is an
experimental proxy transport, not a production service.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
from aiohttp import web, WSMsgType

HOST = "127.0.0.1"
PORT = 8765
TOKEN = os.environ.get("E3_TUNNEL_TOKEN", "")
MAX_CONNECTIONS = 20
CONNECT_TIMEOUT = 8
IDLE_TIMEOUT = 300
slots = asyncio.Semaphore(MAX_CONNECTIONS)


def is_public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return ip.is_global and not (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


async def open_public_destination(host: str, port: int):
    if not host or len(host) > 253 or port not in (80, 443):
        raise ValueError("Only destination TCP ports 80 and 443 are allowed")
    if any(ord(ch) < 32 for ch in host) or "/" in host or "\\" in host:
        raise ValueError("Invalid destination host")

    loop = asyncio.get_running_loop()
    infos = await asyncio.wait_for(
        loop.getaddrinfo(host, port, type=socket.SOCK_STREAM),
        timeout=CONNECT_TIMEOUT,
    )
    candidates = []
    for family, socktype, proto, _canonname, sockaddr in infos:
        try:
            if is_public_ip(sockaddr[0]):
                candidates.append((family, socktype, proto, sockaddr))
        except ValueError:
            continue
    if not candidates:
        raise ValueError("Destination did not resolve to a public IP")

    last_error = None
    for family, socktype, proto, sockaddr in candidates:
        sock = socket.socket(family, socktype, proto)
        sock.setblocking(False)
        try:
            await asyncio.wait_for(loop.sock_connect(sock, sockaddr), timeout=CONNECT_TIMEOUT)
            reader, writer = await asyncio.open_connection(sock=sock)
            return reader, writer
        except Exception as exc:
            last_error = exc
            sock.close()
    raise OSError(f"Could not connect to public destination: {last_error}")


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"service": "Freeman E3 relay", "status": "ok"})


async def relay(request: web.Request) -> web.StreamResponse:
    if not TOKEN:
        raise web.HTTPServiceUnavailable(text="E3 token is not configured")
    if request.headers.get("Authorization", "") != f"Bearer {TOKEN}":
        raise web.HTTPUnauthorized(text="Invalid E3 token")
    if request.headers.get("Upgrade", "").lower() != "websocket":
        raise web.HTTPBadRequest(text="WebSocket upgrade required")

    ws = web.WebSocketResponse(max_msg_size=131072, heartbeat=30, autoping=True)
    await ws.prepare(request)
    writer = None
    try:
        async with slots:
            first = await asyncio.wait_for(ws.receive(), timeout=10)
            if first.type != WSMsgType.TEXT:
                await ws.close(message=b"Expected destination JSON")
                return ws
            try:
                spec = json.loads(first.data)
                host = str(spec["host"])
                port = int(spec["port"])
                if port not in (80, 443):
                    raise ValueError("Only ports 80 and 443 are permitted")
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                await ws.close(message=b"Invalid destination")
                return ws

            try:
                reader, writer = await open_public_destination(host, port)
            except Exception:
                await ws.send_str(json.dumps({"error": "destination_rejected_or_unreachable"}))
                await ws.close()
                return ws

            await ws.send_str(json.dumps({"status": "connected"}))

            async def tcp_to_ws():
                while not ws.closed:
                    data = await asyncio.wait_for(reader.read(65536), timeout=IDLE_TIMEOUT)
                    if not data:
                        break
                    await ws.send_bytes(data)

            async def ws_to_tcp():
                async for message in ws:
                    if message.type == WSMsgType.BINARY:
                        writer.write(message.data)
                        await writer.drain()
                    elif message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
                    elif message.type == WSMsgType.TEXT:
                        break

            tasks = [asyncio.create_task(tcp_to_ws()), asyncio.create_task(ws_to_tcp())]
            _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        if not ws.closed:
            await ws.close()
    return ws


def main() -> None:
    if not TOKEN or len(TOKEN) < 32:
        raise SystemExit("E3_TUNNEL_TOKEN must be configured and at least 32 characters")
    app = web.Application(client_max_size=4096)
    app.router.add_get("/health", health)
    app.router.add_get("/tunnel", relay)
    web.run_app(app, host=HOST, port=PORT, print=None)


if __name__ == "__main__":
    main()
