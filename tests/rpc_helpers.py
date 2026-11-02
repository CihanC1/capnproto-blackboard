"""Helpers for tests that need a real Cap'n Proto RPC server.

pycapnp requires every RPC call to run inside ``capnp.kj_loop()``, so the
helpers wrap server and client in one async context manager and each test keeps
all its work inside a single task.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import capnp

from capnproto_demo.client import RemoteDemo
from capnproto_demo.server import DemoServer


@asynccontextmanager
async def running_server(root, latency: float = 0.0):
    """Start a server on a free port (port 0 lets the OS choose)."""
    async with capnp.kj_loop():
        server = DemoServer("127.0.0.1", 0, root, latency)
        await server.start()
        try:
            yield server
        finally:
            await server.stop()


@asynccontextmanager
async def client_for(server: DemoServer):
    remote = await RemoteDemo.connect("127.0.0.1", server.port, timeout=5)
    try:
        yield remote
    finally:
        await remote.close()
