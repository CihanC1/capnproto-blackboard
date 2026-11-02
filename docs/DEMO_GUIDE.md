# Demo Guide

Exact commands for a live demo. Use **three terminals**: Server, Client A, Client B.
Outputs below were captured from real runs on Windows 11 / Python 3.12.7 / pycapnp 2.2.4 (file sizes
and timings can differ slightly on your machine).

## 0. Before the presentation

```powershell
cd capnproto-blackboard
.\.venv\Scripts\python.exe -m pytest -q        # optional safety check: expect all tests to pass
```

Make the terminal font large (Ctrl+= in Windows Terminal) and put the three windows side by side.

## 1. Install

```powershell
cd capnproto-blackboard
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -c "import capnp; print(capnp.__version__)"      # prints 2.2.4 (or newer 2.x)
```

On Linux/macOS use `source .venv/bin/activate`.

## 2. Start the server (Terminal 1)

```powershell
.\.venv\Scripts\Activate.ps1
python -m capnproto_demo.server --port 60000
```

Expected:

```
Cap'n Proto RPC server listening on port 60000
  address     : 127.0.0.1:60000
  shared root : ...\capnproto-blackboard\demo\shared-files
  press Ctrl+C to stop
```

The server also logs every request (`[client-1] put 'topic' = ... -> created`). This is useful to
show the audience that the work really happens on the server.

## 3. Start Client A (Terminal 2)

```powershell
.\.venv\Scripts\Activate.ps1
python -m capnproto_demo.client --name A
```

```
Connected. Type 'help' for commands, 'exit' to quit.
[A]>
```

## 4. Start Client B (Terminal 3)

```powershell
.\.venv\Scripts\Activate.ps1
python -m capnproto_demo.client --name B
```

## 5. Blackboard demonstration

| Step | Terminal | Type | Expected output |
|------|----------|------|-----------------|
| 1 | A | `put topic "Distributed Systems"` | `Success: value stored.` |
| 2 | B | `get topic` | `Distributed Systems` |
| 3 | B | `put topic "Cap'n Proto RPC"` | `Success: value updated.` |
| 4 | A | `get topic` | `Cap'n Proto RPC` |
| 5 | A | `list` | `topic = Cap'n Proto RPC` |
| 6 | B | `get unknown` | `Key 'unknown' does not exist on the blackboard.` |
| 7 | B | `remove topic` | `Success: key removed.` |
| 8 | A | `list` | `(the blackboard is empty)` |

Point out: A and B never connect to each other. Show the server window: every operation is logged
there with its client label.

Server log for these steps (abridged):

```
[client-1] put 'topic' = 'Distributed Systems' -> created
[client-2] get 'topic' -> 'Distributed Systems'
[client-2] put 'topic' = "Cap'n Proto RPC" -> updated
[client-1] get 'topic' -> "Cap'n Proto RPC"
```

## 6. Remote directory listing

In either client:

```
[A]> ls .
Remote directory contents:

documents     DIRECTORY
config.json   FILE       136 bytes
example.txt   FILE       125 bytes

[A]> ls documents
Remote directory contents:

notes.md     FILE       87 bytes
slides.txt   FILE       59 bytes
```

Security demonstration (also shows error handling):

```
[A]> ls ../..
Error: access to '../..' is not allowed (only paths inside the shared directory are permitted)
[A]> ls nope
Error: 'nope' does not exist
[A]> ls example.txt
Error: 'example.txt' is not a directory
```

Prove it is the *server's* disk: in a fourth terminal (or Explorer) create a file in
`demo\shared-files`, run `ls .` again: the new file shows up.

## 7. Optional: asynchronous RPC

Stop the server (Ctrl+C) and start it with an artificial delay:

```powershell
python -m capnproto_demo.server --port 60000 --latency 0.5
```

In a client:

```
[A]> async-demo
Sequential: 3 puts, each awaited before the next -> 1.55s
Concurrent: 3 requests sent in 0.1 ms, none answered yet (client is NOT blocked)
Concurrent: all 3 replies received after       -> 0.52s
(demo entries removed again)
```

Message: three requests were *in flight at the same time*; the client waited only once.

## 8. Optional: server failure

With a client connected, press Ctrl+C in the server window. In the client type `get topic`:
expected `Error: connection to the server was lost` (the session stays open; type `exit`).
Starting a client with no server running prints
`Error: cannot connect to 127.0.0.1:60000 (...). Is the server running?`.

## 9. Shut down

1. In each client type `exit` (prints `Goodbye.`) or press Ctrl+D.
2. In the server window press Ctrl+C (the server stops and prints `Server stopped.`).
   Note: the Ctrl+C path is not covered by the automated tests; rehearse it once.

## 10. Running on two machines

Server machine: `python -m capnproto_demo.server --host 0.0.0.0 --port 60000` (allow the port in the
firewall). Client machine: `python -m capnproto_demo.client --host <server-ip> --port 60000`.

## Problems and solutions

| Problem | Solution |
|---------|----------|
| Port already in use | `python -m capnproto_demo.server --port 60010` and the same `--port` on the clients; or find the process with `netstat -ano \| findstr 60000`. |
| Client: `cannot connect` | Server not started, wrong port, or wrong host. Use `127.0.0.1` for local demos. |
| Remote client cannot connect | Server must use `--host 0.0.0.0`; check firewall and IP address. |
| `ModuleNotFoundError` | Activate the venv in **each** terminal. |
| `Cap'n Proto schema not found` | Install with `pip install -e .` from the repository root. |
| Values with spaces are rejected | Quote them: `put topic "two words"`. |
| Stale state between rehearsals | Restart the server; the blackboard is memory-only. |
| Slow first start | The first import of pycapnp and the schema may take a moment; start everything before the talk. |
