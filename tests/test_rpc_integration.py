"""Integration tests: every call below travels through real Cap'n Proto RPC over TCP."""

import asyncio
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import capnp
import pytest

from capnproto_demo.client import ConnectionFailed, RemoteDemo, RpcError, Shell
from capnproto_demo.filesystem import EntryType, ListStatus
from rpc_helpers import client_for, running_server

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def shared(tmp_path):
    root = tmp_path / "shared"
    (root / "documents").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "example.txt").write_text("hello")
    (root / "config.json").write_text("{}")
    (tmp_path / "outside.txt").write_text("secret")
    return root


# -- startup / connection ---------------------------------------------------


async def test_server_starts_and_accepts_connection(shared):
    async with running_server(shared) as server:
        assert server.port > 0
        async with client_for(server) as remote:
            assert await remote.list() == []


async def test_connection_failure_is_reported_clearly():
    with socket.socket() as s:  # find a port nobody listens on
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    async with capnp.kj_loop():
        with pytest.raises(ConnectionFailed, match="Is the server running"):
            await RemoteDemo.connect("127.0.0.1", free_port, timeout=10)


async def test_calls_fail_cleanly_after_server_stops(shared):
    async with running_server(shared) as server:
        async with client_for(server) as remote:
            await remote.put("a", "1")
            await server.stop()
            await asyncio.sleep(0.2)
            with pytest.raises(RpcError):
                await remote.get("a")


# -- blackboard over RPC ----------------------------------------------------


async def test_put_get_update_remove_list(shared):
    async with running_server(shared) as server, client_for(server) as c:
        assert await c.put("topic", "Distributed Systems") == "created"
        assert (await c.get("topic")).value == "Distributed Systems"
        assert await c.put("topic", "Cap'n Proto RPC") == "updated"
        assert await c.list() == [("topic", "Cap'n Proto RPC")]
        assert await c.remove("topic") is True
        assert await c.remove("topic") is False
        assert await c.list() == []


async def test_missing_key_differs_from_empty_value(shared):
    async with running_server(shared) as server, client_for(server) as c:
        assert (await c.get("nope")).found is False
        await c.put("empty", "")
        result = await c.get("empty")
        assert result.found is True and result.value == ""


async def test_unicode_and_large_values_round_trip(shared):
    value = "Grüße 🌍 — " + "x" * 200_000
    async with running_server(shared) as server, client_for(server) as c:
        await c.put("big", value)
        assert (await c.get("big")).value == value


async def test_empty_key_is_rejected_by_server(shared):
    async with running_server(shared) as server, client_for(server) as c:
        with pytest.raises(RpcError, match="key must not be empty"):
            await c.put("", "x")


async def test_two_clients_share_one_blackboard(shared):
    """The scenario from the seminar: A and B communicate only via the server."""
    async with running_server(shared) as server, \
            client_for(server) as a, client_for(server) as b:
        await a.put("topic", "Distributed Systems")
        assert (await b.get("topic")).value == "Distributed Systems"
        assert await b.put("topic", "Cap'n Proto RPC") == "updated"
        assert (await a.get("topic")).value == "Cap'n Proto RPC"
        assert await a.list() == [("topic", "Cap'n Proto RPC")]
        assert server.blackboard.get("topic") == "Cap'n Proto RPC"  # one shared object


async def test_late_joining_client_sees_existing_state(shared):
    async with running_server(shared) as server:
        async with client_for(server) as first:
            await first.put("k", "v")
        async with client_for(server) as second:  # new connection, same board
            assert (await second.get("k")).value == "v"


async def test_many_concurrent_clients_do_not_lose_writes(shared):
    async with running_server(shared) as server:
        clients = [RemoteDemo.connect("127.0.0.1", server.port) for _ in range(5)]
        clients = await asyncio.gather(*clients)
        try:
            await asyncio.gather(*(
                c.put(f"c{i}-{n}", str(n))
                for i, c in enumerate(clients) for n in range(20)
            ))
            entries = await clients[0].list()
            assert len(entries) == 100
        finally:
            for c in clients:
                await c.close()


async def test_concurrent_writes_to_same_key_end_in_one_consistent_value(shared):
    async with running_server(shared) as server, \
            client_for(server) as a, client_for(server) as b:
        await asyncio.gather(*(
            (a if n % 2 else b).put("race", f"v{n}") for n in range(40)
        ))
        value_seen_by_a = (await a.get("race")).value
        assert value_seen_by_a == (await b.get("race")).value
        assert value_seen_by_a in {f"v{n}" for n in range(40)}


async def test_requests_are_asynchronous(shared):
    """Three 0.3 s requests overlap: ~0.3 s in total, not ~0.9 s."""
    async with running_server(shared, latency=0.3) as server, client_for(server) as c:
        start = time.perf_counter()
        promises = [c.put_promise(f"k{i}", "v") for i in range(3)]
        sent_after = time.perf_counter() - start
        assert sent_after < 0.1          # sending did not block
        for p in promises:
            await RemoteDemo._await(p)
        assert time.perf_counter() - start < 0.8


# -- directory listing over RPC ---------------------------------------------


async def test_remote_directory_listing(shared):
    async with running_server(shared) as server, client_for(server) as c:
        listing = await c.ls(".")
        assert listing.status is ListStatus.OK
        assert [(e.name, e.type) for e in listing.entries] == [
            ("documents", EntryType.DIRECTORY),
            ("empty", EntryType.DIRECTORY),
            ("config.json", EntryType.FILE),
            ("example.txt", EntryType.FILE),
        ]
        assert {e.name: e.size for e in listing.entries}["example.txt"] == 5


async def test_remote_listing_empty_and_nonexistent(shared):
    async with running_server(shared) as server, client_for(server) as c:
        assert (await c.ls("empty")).entries == ()
        assert (await c.ls("empty")).status is ListStatus.OK
        assert (await c.ls("missing")).status is ListStatus.NOT_FOUND
        assert (await c.ls("example.txt")).status is ListStatus.NOT_A_DIRECTORY


@pytest.mark.parametrize("path", ["..", "../outside.txt", "/", "C:\\Windows", "documents/../.."])
async def test_remote_listing_rejects_paths_outside_root(shared, path):
    async with running_server(shared) as server, client_for(server) as c:
        listing = await c.ls(path)
        assert listing.status is ListStatus.ACCESS_DENIED
        assert listing.entries == ()


async def test_server_sees_live_filesystem_changes(shared):
    async with running_server(shared) as server, client_for(server) as c:
        before = {e.name for e in (await c.ls(".")).entries}
        (shared / "new.txt").write_text("x")
        after = {e.name for e in (await c.ls(".")).entries}
        assert after - before == {"new.txt"}


# -- the interactive shell (parsing + formatting) ---------------------------


async def test_shell_reproduces_the_demo_transcript(shared):
    async with running_server(shared) as server, \
            client_for(server) as ra, client_for(server) as rb:
        a, b = Shell(ra), Shell(rb)
        assert (await a.execute('put topic "Distributed Systems"'))[0] == "Success: value stored."
        assert (await b.execute("get topic"))[0] == "Distributed Systems"
        assert (await b.execute('put topic "Cap\'n Proto RPC"'))[0] == "Success: value updated."
        assert (await a.execute("get topic"))[0] == "Cap'n Proto RPC"
        assert (await a.execute("list"))[0] == "topic = Cap'n Proto RPC"


async def test_shell_errors_and_ls_output(shared):
    async with running_server(shared) as server, client_for(server) as remote:
        shell = Shell(remote)
        assert "Usage: put" in (await shell.execute("put onlykey"))[0]
        assert "could not parse" in (await shell.execute('put "unterminated'))[0]
        assert "Unknown command" in (await shell.execute("frobnicate"))[0]
        assert "does not exist" in (await shell.execute("get ghost"))[0]
        assert (await shell.execute("get ghost"))[1] is True      # session stays alive
        assert (await shell.execute("   "))[0] == ""
        assert (await shell.execute("exit"))[1] is False
        out = (await shell.execute("ls"))[0]
        assert out.startswith("Remote directory contents:")
        assert re.search(r"documents\s+DIRECTORY", out)
        assert re.search(r"example\.txt\s+FILE", out)
        assert "not allowed" in (await shell.execute("ls ../.."))[0]
        assert "(empty directory)" in (await shell.execute("ls empty"))[0]


# -- real, separate OS processes --------------------------------------------


def test_server_and_client_as_separate_processes(shared):
    """Start the real server CLI and drive it with two real client CLI processes."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    server = subprocess.Popen(
        [sys.executable, "-m", "capnproto_demo.server", "--port", "0", "--root", str(shared)],
        cwd=PROJECT_ROOT, env=env, stdout=subprocess.PIPE, text=True,
    )
    try:
        port = None
        for line in server.stdout:
            m = re.search(r"listening on port (\d+)", line)
            if m:
                port = m.group(1)
                break
        assert port, "server did not announce its port"

        def run_client(*commands):
            args = [sys.executable, "-m", "capnproto_demo.client", "--port", port]
            for c in commands:
                args += ["-c", c]
            return subprocess.run(args, cwd=PROJECT_ROOT, env=env, capture_output=True,
                                  text=True, timeout=30)

        a = run_client('put topic "Distributed Systems"')
        assert "Success: value stored." in a.stdout, a.stderr
        b = run_client("get topic", 'put topic "Cap\'n Proto RPC"')
        assert "Distributed Systems" in b.stdout and "value updated" in b.stdout
        c = run_client("list", "ls .")
        assert "topic = Cap'n Proto RPC" in c.stdout
        assert "example.txt" in c.stdout
    finally:
        server.terminate()
        server.wait(timeout=10)


def test_client_cli_exits_with_error_when_no_server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    result = subprocess.run(
        [sys.executable, "-m", "capnproto_demo.client", "--port", str(port), "-c", "list"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1
    assert "cannot connect" in result.stderr
