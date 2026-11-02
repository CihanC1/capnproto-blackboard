"""Interactive Cap'n Proto RPC client for the blackboard / directory demo.

Start it with::

    python -m capnproto_demo.client --host 127.0.0.1 --port 60000 --name A

The file has two layers:

* ``RemoteDemo``   - a thin, typed wrapper around the RPC capabilities.
* ``Shell``        - the command-line interpreter (help, put, get, ...).
"""

from __future__ import annotations

import argparse
import asyncio
import shlex
import sys
import time
from dataclasses import dataclass

import capnp

from .filesystem import DirEntry, DirListing, EntryType, ListStatus
from .protocol import DEFAULT_PORT, load_schema

CONNECT_TIMEOUT = 5.0


class RpcError(Exception):
    """A remote call failed (server error, connection lost, ...)."""


class ConnectionFailed(RpcError):
    """The client could not connect to the server."""


@dataclass(frozen=True)
class Lookup:
    found: bool
    value: str = ""


# ---------------------------------------------------------------------------
# RPC wrapper
# ---------------------------------------------------------------------------


class RemoteDemo:
    """A connection to the server plus the two capabilities it provides."""

    def __init__(self, stream, client, board, files) -> None:
        self._stream = stream
        self._client = client
        self._board = board      # capability: remote Blackboard
        self._files = files      # capability: remote FileBrowser

    @classmethod
    async def connect(cls, host: str, port: int, timeout: float = CONNECT_TIMEOUT) -> "RemoteDemo":
        """Open a TCP connection and fetch the bootstrap capability.

        Must be called inside ``capnp.kj_loop()`` (``capnp.run()`` does that).
        """
        schema = load_schema()
        try:
            stream = await asyncio.wait_for(
                capnp.AsyncIoStream.create_connection(host=host, port=port), timeout
            )
        except asyncio.TimeoutError as exc:
            raise ConnectionFailed(
                f"timed out connecting to {host}:{port}. Is the server running and reachable?"
            ) from exc
        except OSError as exc:
            raise ConnectionFailed(
                f"cannot connect to {host}:{port} ({exc.strerror or exc}). "
                "Is the server running?"
            ) from exc

        client = capnp.TwoPartyClient(stream)
        service = client.bootstrap().cast_as(schema.DemoService)
        # "Promise pipelining": we call a method on the *promised* result of
        # service.blackboard() without waiting for the first reply.
        board = service.blackboard().board
        files = service.files().browser
        return cls(stream, client, board, files)

    async def close(self) -> None:
        self._board = self._files = None
        self._client.close()
        self._stream.close()
        await asyncio.sleep(0)

    # -- helper -------------------------------------------------------------

    @staticmethod
    async def _await(promise):
        try:
            return await promise
        except capnp.KjException as exc:
            raise RpcError(_describe_kj_error(exc)) from exc

    # -- blackboard ---------------------------------------------------------

    def put_promise(self, key: str, value: str):
        """Send ``put`` and return the promise immediately (does not wait)."""
        return self._board.put(key, value)

    async def put(self, key: str, value: str) -> str:
        """Return ``"created"`` or ``"updated"``."""
        return str((await self._await(self.put_promise(key, value))).status)

    async def get(self, key: str) -> Lookup:
        result = await self._await(self._board.get(key))
        if result.lookup.which() == "found":
            return Lookup(True, result.lookup.found)
        return Lookup(False)

    async def remove(self, key: str) -> bool:
        return bool((await self._await(self._board.remove(key))).removed)

    async def list(self) -> list[tuple[str, str]]:
        result = await self._await(self._board.list())
        return [(e.key, e.value) for e in result.entries]

    # -- directory listing --------------------------------------------------

    async def ls(self, path: str = ".") -> DirListing:
        result = await self._await(self._files.listDirectory(path))
        listing = result.listing
        return DirListing(
            ListStatus(str(listing.status)),
            listing.message,
            tuple(
                DirEntry(e.name, EntryType(str(e.type)), e.size) for e in listing.entries
            ),
        )


def _describe_kj_error(exc: capnp.KjException) -> str:
    text = str(exc)
    if "remote exception:" in text:
        # Server-side error; keep only the useful message line.
        for line in text.splitlines():
            if "ValueError:" in line:
                return line.split("ValueError:", 1)[1].strip()
    if getattr(exc, "type", "") == "DISCONNECTED" or "disconnected" in text.lower():
        return "connection to the server was lost"
    return text.splitlines()[0] if text else "remote call failed"


# ---------------------------------------------------------------------------
# Command-line shell
# ---------------------------------------------------------------------------

HELP_TEXT = """\
Commands:
  help                  show this help
  put <key> <value>     create or update an entry (quote values with spaces)
  get <key>             read an entry
  remove <key>          delete an entry
  list                  show all entries on the blackboard
  ls [directory]        list a directory on the SERVER (relative to its shared root)
  async-demo            show asynchronous RPC: sequential vs. concurrent calls
  exit                  leave the client (also: quit, Ctrl+D)"""


class Shell:
    """Parses one command line at a time and talks to the server."""

    def __init__(self, remote: RemoteDemo) -> None:
        self.remote = remote

    async def execute(self, line: str) -> tuple[str, bool]:
        """Run one command. Return ``(text_to_print, keep_running)``."""
        try:
            parts = shlex.split(line)
        except ValueError as exc:
            return f"Error: could not parse input ({exc}).", True
        if not parts:
            return "", True

        command, args = parts[0].lower(), parts[1:]
        try:
            if command in ("exit", "quit"):
                return "Goodbye.", False
            if command == "help":
                return HELP_TEXT, True
            if command == "put":
                return await self._put(args), True
            if command == "get":
                return await self._get(args), True
            if command == "remove":
                return await self._remove(args), True
            if command == "list":
                return await self._list(args), True
            if command == "ls":
                return await self._ls(args), True
            if command == "async-demo":
                return await self._async_demo(), True
            return f"Unknown command '{command}'. Type 'help' for a list of commands.", True
        except RpcError as exc:
            return f"Error: {exc}", True

    # -- individual commands -----------------------------------------------

    async def _put(self, args: list[str]) -> str:
        if len(args) != 2:
            return 'Usage: put <key> <value>   (quote values with spaces: put topic "Distributed Systems")'
        status = await self.remote.put(args[0], args[1])
        return "Success: value stored." if status == "created" else "Success: value updated."

    async def _get(self, args: list[str]) -> str:
        if len(args) != 1:
            return "Usage: get <key>"
        found = await self.remote.get(args[0])
        if not found.found:
            return f"Key '{args[0]}' does not exist on the blackboard."
        # An empty value is valid and different from a missing key.
        return found.value if found.value else "(empty string)"

    async def _remove(self, args: list[str]) -> str:
        if len(args) != 1:
            return "Usage: remove <key>"
        if await self.remote.remove(args[0]):
            return "Success: key removed."
        return f"Key '{args[0]}' does not exist on the blackboard."

    async def _list(self, args: list[str]) -> str:
        if args:
            return "Usage: list"
        entries = await self.remote.list()
        if not entries:
            return "(the blackboard is empty)"
        return "\n".join(f"{key} = {value}" for key, value in entries)

    async def _ls(self, args: list[str]) -> str:
        if len(args) > 1:
            return "Usage: ls [directory]   (quote names with spaces)"
        path = args[0] if args else "."
        listing = await self.remote.ls(path)
        if listing.status is not ListStatus.OK:
            return f"Error: {listing.message}"
        lines = ["Remote directory contents:", ""]
        if not listing.entries:
            lines.append("(empty directory)")
        width = max((len(e.name) for e in listing.entries), default=0) + 3
        for entry in listing.entries:
            size = f"{entry.size} bytes" if entry.type is EntryType.FILE else ""
            lines.append(f"{entry.name:<{width}}{entry.type.name:<11}{size}".rstrip())
        return "\n".join(lines)

    async def _async_demo(self) -> str:
        """Contrast "await each call" with "start all calls, then await".

        Start the server with ``--latency 0.5`` to see a clear difference.
        """
        keys = [f"async-demo-{i}" for i in range(1, 4)]
        out: list[str] = []

        t0 = time.perf_counter()
        for key in keys:
            await self.remote.put(key, "sequential")
        sequential = time.perf_counter() - t0
        out.append(f"Sequential: 3 puts, each awaited before the next -> {sequential:.2f}s")

        t0 = time.perf_counter()
        promises = [self.remote.put_promise(key, "concurrent") for key in keys]
        sent = time.perf_counter() - t0
        out.append(f"Concurrent: 3 requests sent in {sent * 1000:.1f} ms, none answered yet "
                   "(client is NOT blocked)")
        for promise in promises:
            await RemoteDemo._await(promise)
        concurrent = time.perf_counter() - t0
        out.append(f"Concurrent: all 3 replies received after       -> {concurrent:.2f}s")

        await asyncio.gather(*(self.remote.remove(key) for key in keys))
        out.append("(demo entries removed again)")
        return "\n".join(out)


async def _interactive(shell: Shell, prompt: str) -> None:
    print("Connected. Type 'help' for commands, 'exit' to quit.")
    while True:
        try:
            # input() blocks, so run it in a thread; the event loop (and with it
            # the Cap'n Proto connection) stays alive while we wait for the user.
            line = await asyncio.to_thread(input, prompt)
        except EOFError:
            print()
            return
        output, keep_going = await shell.execute(line)
        if output:
            print(output)
        if not keep_going:
            return


async def _run(args: argparse.Namespace) -> int:
    try:
        remote = await RemoteDemo.connect(args.host, args.port)
    except ConnectionFailed as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    shell = Shell(remote)
    try:
        if args.command:
            for line in args.command:
                print(f"> {line}")
                output, keep_going = await shell.execute(line)
                if output:
                    print(output)
                if not keep_going:
                    break
        else:
            prompt = f"[{args.name}]> " if args.name else "> "
            await _interactive(shell, prompt)
    finally:
        await remote.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capnproto_demo.client",
        description="Interactive client for the Cap'n Proto blackboard / directory demo.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="server host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"server port (default {DEFAULT_PORT})")
    parser.add_argument("--name", default="", help="label shown in the prompt, e.g. A or B")
    parser.add_argument("-c", "--command", action="append",
                        help="run this command and exit (repeatable); skips interactive mode")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(capnp.run(_run(args)))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
