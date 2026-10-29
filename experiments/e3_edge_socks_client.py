#!/usr/bin/env python3
"""Local-only SOCKS5 client that transports TCP via the E3 WebSocket relay."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import struct
import sys
from urllib.parse import urlsplit

import aiohttp

LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 1080
CONNECT_TIMEOUT = 12


def parse_tunnel_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise ValueError("Provide the HTTPS trycloudflare URL printed by the workflow")
    if parsed.username or parsed.password or parsed.port:
        raise ValueError("Tunnel URL must not include credentials or a port")
    return f"wss://{parsed.hostname}/tunnel"


async def read_exact(reader: asyncio.StreamReader, count: int) -> bytes:
    data = await reader.readexactly(count)
    return data


async def socks_reply(writer: asyncio.StreamWriter, code: int) -> None:
    writer.write(b"\x05" + bytes([code]) + b"\x00\x01\x00\x00\x00\x00\x00\x00")
    await writer.drain()


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                        tunnel_ws_url: str, token: str, session: aiohttp.ClientSession) -> None:
    remote_writer = None
    ws = None
    try:
        # SOCKS5 greeting: version, number of methods, methods.
        greeting = await asyncio.wait_for(read_exact(reader, 2), timeout=10)
        if greeting[0] != 5:
            return
        methods = await read_exact(reader, greeting[1])
        if 0 not in methods:
            writer.write(b"\x05\xff")
            await writer.drain()
            return
        writer.write(b"\x05\x00")
        await writer.drain()

        head = await asyncio.wait_for(read_exact(reader, 4), timeout=10)
        version, command, _reserved, atyp = head
        if version != 5 or command != 1:
            await socks_reply(writer, 7)  # command not supported
            return
        if atyp == 1:
            host = socket.inet_ntop(socket.AF_INET, await read_exact(reader, 4))
        elif atyp == 4:
            host = socket.inet_ntop(socket.AF_INET6, await read_exact(reader, 16))
        elif atyp == 3:
            size = (await read_exact(reader, 1))[0]
            host = (await read_exact(reader, size)).decode("idna")
        else:
            await socks_reply(writer, 8)
            return
        port = struct.unpack("!H", await read_exact(reader, 2))[0]
        if port not in (80, 443):
            await socks_reply(writer, 2)  # connection not allowed by ruleset
            return

        # Reject obvious local/private literal IPs before sending anything remotely.
        try:
            ip = ipaddress.ip_address(host)
            if not ip.is_global:
                await socks_reply(writer, 2)
                return
        except ValueError:
            pass

        async with asyncio.timeout(CONNECT_TIMEOUT):
            ws = await session.ws_connect(
                tunnel_ws_url,
                headers={"Authorization": f"Bearer {token}"},
                heartbeat=30,
                max_msg_size=0,
            )
            await ws.send_json({"host": host, "port": port})
            first = await ws.receive()
        if first.type != aiohttp.WSMsgType.TEXT:
            await socks_reply(writer, 5)
            return
        try:
            status = json.loads(first.data)
        except json.JSONDecodeError:
            status = {}
        if status.get("status") != "connected":
            await socks_reply(writer, 5)
            return

        await socks_reply(writer, 0)

        async def local_to_remote():
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                await ws.send_bytes(data)

        async def remote_to_local():
            async for message in ws:
                if message.type == aiohttp.WSMsgType.BINARY:
                    writer.write(message.data)
                    await writer.drain()
                elif message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED,
                                      aiohttp.WSMsgType.ERROR):
                    break
                elif message.type == aiohttp.WSMsgType.TEXT:
                    break

        tasks = [asyncio.create_task(local_to_remote()),
                 asyncio.create_task(remote_to_local())]
        _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError,
            OSError, aiohttp.ClientError, ValueError) as exc:
        print(f"[E3 client] connection ended: {type(exc).__name__}", file=sys.stderr)
    finally:
        if ws is not None and not ws.closed:
            await ws.close()
        if remote_writer is not None:
            remote_writer.close()
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python experiments/e3_edge_socks_client.py https://<id>.trycloudflare.com")
    token = os.environ.get("E3_TUNNEL_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("Set E3_TUNNEL_TOKEN in this terminal; never put it on the command line")
    tunnel_ws_url = parse_tunnel_url(sys.argv[1])
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=CONNECT_TIMEOUT)
    connector = aiohttp.TCPConnector(limit=50, limit_per_host=50)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        server = await asyncio.start_server(
            lambda r, w: handle_client(r, w, tunnel_ws_url, token, session),
            host=LOCAL_HOST,
            port=LOCAL_PORT,
            limit=2**16,
        )
        print(f"E3 SOCKS5 listening on {LOCAL_HOST}:{LOCAL_PORT}")
        print("Configure the dedicated Edge instance to use SOCKS5 127.0.0.1:1080.")
        print("Only TCP destinations on ports 80 and 443 are permitted.")
        async with server:
            await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nE3 SOCKS5 client stopped.")
