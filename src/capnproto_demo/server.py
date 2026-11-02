"""Cap'n Proto RPC server: one process, one shared blackboard, one file browser.

Run it with::

    python -m capnproto_demo.server --port 60000

How the pieces fit together
---------------------------
* ``protocol.load_schema()`` loads ``schema/blackboard.capnp`` at run time.
* ``BlackboardServicer`` / ``FileBrowserServicer`` / ``DemoServiceServicer``
  implement the interfaces from the schema. Each one inherits from the
  ``.Server`` class that pycapnp generates for the matching interface.
* ``DemoServer`` opens a TCP listener. For every incoming connection it creates
  a ``capnp.TwoPartyServer`` whose *bootstrap* capability is a
  ``DemoServiceServicer``. All connections receive the SAME ``Blackboard``
  object, which is what makes the blackboard shared.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import logging
import sys
from pathlib import Path

import capnp

from .blackboard import Blackboard
from .filesystem import DirectoryLister
from .protocol import DEFAULT_PORT, DEFAULT_SHARED_ROOT, load_schema

log = logging.getLogger("capnproto_demo.server")

_schema = load_schema()


def _require_key(key: str) -> None:
    if not key:
        raise ValueError("key must not be empty")


# ---------------------------------------------------------------------------
# Interface implementations
# ---------------------------------------------------------------------------
# pycapnp rules (v2.x):
#   * every RPC method is a coroutine (``async def``);
#   * parameters arrive as keyword arguments named exactly like in the schema;
#   * ``_context`` gives access to the results struct;
#   * a plain ``return value`` fills the (single) result field.


class BlackboardServicer(_schema.Blackboard.Server):
    """Remote face of the blackboard. Holds a reference, never a copy."""

    def __init__(self, board: Blackboard, label: str, latency: float = 0.0) -> None:
        self._board = board
        self._label = label
        self._latency = latency

    async def _simulate_work(self) -> None:
        # Optional artificial delay so that the asynchronous-RPC demo is visible.
        if self._latency > 0:
            await asyncio.sleep(self._latency)

    async def put(self, key, value, _context, **kwargs):
        _require_key(key)
        await self._simulate_work()
        created = self._board.put(key, value)
        log.info("[%s] put %r = %r -> %s", self._label, key, value,
                 "created" if created else "updated")
        return "created" if created else "updated"

    async def get(self, key, _context, **kwargs):
        _require_key(key)
        await self._simulate_work()
        value = self._board.get(key)
        # Union: either `found` carries the text or `missing` is set.
        # An empty string is a valid "found" value, so we test for None.
        if value is None:
            _context.results.lookup.missing = None
            log.info("[%s] get %r -> missing", self._label, key)
        else:
            _context.results.lookup.found = value
            log.info("[%s] get %r -> %r", self._label, key, value)

    async def remove(self, key, _context, **kwargs):
        _require_key(key)
        await self._simulate_work()
        removed = self._board.remove(key)
        log.info("[%s] remove %r -> %s", self._label, key,
                 "removed" if removed else "not found")
        return removed

    async def list(self, _context, **kwargs):
        await self._simulate_work()
        items = self._board.items()
        _context.results.entries = [{"key": k, "value": v} for k, v in items]
        log.info("[%s] list -> %d entries", self._label, len(items))


class FileBrowserServicer(_schema.FileBrowser.Server):
    """Remote directory listing; the real file system is read on the server."""

    def __init__(self, lister: DirectoryLister, label: str) -> None:
        self._lister = lister
        self._label = label

    async def listDirectory(self, path, _context, **kwargs):
        # Directory I/O is blocking, so run it in a worker thread and keep the
        # event loop free for other clients.
        result = await asyncio.to_thread(self._lister.list, path)
        _context.results.listing = {
            "status": result.status.value,
            "message": result.message,
            "entries": [
                {"name": e.name, "type": e.type.value, "size": e.size}
                for e in result.entries
            ],
        }
        log.info("[%s] ls %r -> %s (%d entries)", self._label, path,
                 result.status.value, len(result.entries))


class DemoServiceServicer(_schema.DemoService.Server):
    """Bootstrap capability: hands out the two sub-services."""

    def __init__(self, board: Blackboard, lister: DirectoryLister,
                 label: str, latency: float = 0.0) -> None:
        self._blackboard = BlackboardServicer(board, label, latency)
        self._files = FileBrowserServicer(lister, label)

    async def blackboard(self, _context, **kwargs):
        return self._blackboard

    async def files(self, _context, **kwargs):
        return self._files


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


class DemoServer:
    """Owns the listening socket, the shared blackboard and all connections."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        shared_root: str | Path = DEFAULT_SHARED_ROOT,
        latency: float = 0.0,
    ) -> None:
        self.host = host
        self.port = port
        self.latency = latency
        self.blackboard = Blackboard()            # ONE blackboard for everybody
        self.lister = DirectoryLister(shared_root)
        self._server: asyncio.AbstractServer | None = None
        self._streams: set = set()
        self._counter = itertools.count(1)

    async def _on_connection(self, stream) -> None:
        label = f"client-{next(self._counter)}"
        log.info("%s connected", label)
        self._streams.add(stream)
        try:
            service = DemoServiceServicer(self.blackboard, self.lister, label, self.latency)
            rpc_server = capnp.TwoPartyServer(stream, bootstrap=service)
            await rpc_server.on_disconnect()
        finally:
            self._streams.discard(stream)
            log.info("%s disconnected", label)

    async def start(self) -> None:
        """Open the listening socket. If ``port`` is 0 the OS picks a free port."""
        self._server = await capnp.AsyncIoStream.create_server(
            self._on_connection, self.host, self.port
        )
        self.port = self._server.sockets[0].getsockname()[1]

    async def serve_forever(self) -> None:
        assert self._server is not None, "call start() first"
        await self._server.serve_forever()

    async def stop(self) -> None:
        """Stop accepting connections and close the existing ones."""
        if self._server is None:
            return
        server, self._server = self._server, None
        server.close()
        for stream in list(self._streams):
            stream.close()
        await asyncio.sleep(0)  # let close callbacks run


async def _run(args: argparse.Namespace) -> None:
    server = DemoServer(args.host, args.port, args.root, args.latency)
    await server.start()
    print(f"Cap'n Proto RPC server listening on port {server.port}", flush=True)
    print(f"  address     : {args.host}:{server.port}", flush=True)
    print(f"  shared root : {server.lister.root}", flush=True)
    if args.latency:
        print(f"  latency     : {args.latency}s artificial delay per blackboard call", flush=True)
    print("  press Ctrl+C to stop", flush=True)
    try:
        await server.serve_forever()
    finally:
        await server.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capnproto_demo.server",
        description="Cap'n Proto RPC server with a shared blackboard and a directory browser.",
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="interface to bind (default 127.0.0.1; use 0.0.0.0 for other machines)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"TCP port (default {DEFAULT_PORT})")
    parser.add_argument("--root", default=str(DEFAULT_SHARED_ROOT),
                        help="directory that clients may browse (default: demo/shared-files)")
    parser.add_argument("--latency", type=float, default=0.0,
                        help="artificial delay in seconds per blackboard call (async-RPC demo)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                        datefmt="%H:%M:%S", stream=sys.stdout)
    try:
        asyncio.run(capnp.run(_run(args)))
    except KeyboardInterrupt:
        print("\nServer stopped.")
    except OSError as exc:
        print(f"Error: could not start the server on {args.host}:{args.port}: {exc}",
              file=sys.stderr)
        return 1
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
