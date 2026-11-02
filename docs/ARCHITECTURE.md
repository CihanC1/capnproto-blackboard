# Architecture

## Overview

One **server process** hosts two services behind a single Cap'n Proto interface:

* the **Blackboard**, a key/value store held in the server's memory, and
* the **FileBrowser**, which lists directories below a configured demo root.

Any number of **clients** connect over TCP. Each client has its own connection, but all connections
use the **same** `Blackboard` object. That is what makes the blackboard *shared*.

| Component | File | Responsibility |
|-----------|------|----------------|
| Interface definition | `schema/blackboard.capnp` | Methods, parameters, results |
| Schema loader | `protocol.py` | `capnp.load()` at run time |
| Blackboard | `blackboard.py` | Plain Python dict + lock. No networking. |
| Directory lister | `filesystem.py` | `pathlib` + sandboxing. No networking. |
| Servants | `server.py` | Classes implementing the interfaces; call the two classes above |
| Server lifecycle | `server.py` (`DemoServer`) | Listening socket, connections, shutdown |
| Client library | `client.py` (`RemoteDemo`) | Typed wrapper around the RPC capabilities |
| CLI | `client.py` (`Shell`) | Parses commands, prints results |

Separating "business logic" (`blackboard.py`, `filesystem.py`) from "RPC glue" (`server.py`) means
the logic is unit-testable without a network, and the RPC layer stays thin.

## 1. Client-server communication

```mermaid
flowchart TB
    subgraph Client["Client process (python -m capnproto_demo.client)"]
        CLI["Shell<br/>(shlex, commands)"] --> RD["RemoteDemo"]
        RD --> TPC["capnp.TwoPartyClient"]
    end
    subgraph Server["Server process (python -m capnproto_demo.server)"]
        TPS["capnp.TwoPartyServer<br/>(one per connection)"] --> DS["DemoServiceServicer"]
        DS --> BS["BlackboardServicer"]
        DS --> FBS["FileBrowserServicer"]
        BS --> BB[("Blackboard<br/>ONE instance")]
        FBS --> DL["DirectoryLister"]
        DL --> DISK[("demo/shared-files")]
    end
    TPC <== "TCP + Cap'n Proto RPC messages" ==> TPS
```

Each connection gets its own `TwoPartyServer` and its own servant objects, but they are all
constructed with the same `Blackboard` instance owned by `DemoServer`.

## 2. Two clients, one blackboard

```mermaid
sequenceDiagram
    participant A as Client A
    participant S as Server (Blackboard)
    participant B as Client B
    A->>S: put("topic", "Distributed Systems")
    S-->>A: status = created
    B->>S: get("topic")
    S-->>B: found = "Distributed Systems"
    B->>S: put("topic", "Cap'n Proto RPC")
    S-->>B: status = updated
    A->>S: get("topic")
    S-->>A: found = "Cap'n Proto RPC"
    A->>S: list()
    S-->>A: [ topic = "Cap'n Proto RPC" ]
    Note over A,B: A and B never exchange a single message.<br/>The blackboard on the server is the only meeting point.
```

## 3. RPC request/response lifecycle

```mermaid
sequenceDiagram
    participant UI as Shell
    participant P as Client stub (capability)
    participant N as Network (TCP)
    participant D as Server dispatch
    participant M as Servant method
    participant BB as Blackboard
    UI->>P: board.put("topic", "x")
    Note right of P: returns a promise immediately
    P->>N: Call message (interface, method id, params struct)
    N->>D: bytes arrive
    D->>M: put(key="topic", value="x") (async)
    M->>BB: put("topic", "x")
    BB-->>M: created = True
    M-->>D: return "created"
    D->>N: Return message (results struct)
    N->>P: bytes arrive
    P-->>UI: await promise -> status = created
```

The client can start several calls before the first answer arrives (see `async-demo`). Each call
has an id, so answers can come back in any order.

## 4. Remote directory listing

```mermaid
sequenceDiagram
    participant C as Client
    participant F as FileBrowserServicer
    participant L as DirectoryLister
    participant D as Server disk
    C->>F: listDirectory("documents")
    F->>L: list("documents") (worker thread)
    L->>L: reject absolute paths, resolve(), check inside root
    alt path allowed
        L->>D: Path.iterdir()
        D-->>L: names, types, sizes
        L-->>F: status=ok, entries
    else outside root / missing / not a directory / no permission
        L-->>F: status=accessDenied | notFound | notADirectory | permissionDenied
    end
    F-->>C: DirListing { status, message, entries[] }
    Note over C: The client only sees this struct.<br/>It never touches the server's disk.
```

### Safety rules in `filesystem.py`

1. Absolute paths (`/etc`, `C:\Windows`, UNC paths, `C:`), and NUL characters are rejected outright.
2. The path is joined to the root and passed through `Path.resolve()`, which collapses `..` and
   follows symlinks.
3. The resolved path must satisfy `is_relative_to(root)`. This blocks `..` traversal and symlinks that
   point outside the root with one check.
4. Entries that are themselves symlinks are reported as `SYMLINK` and never followed.
5. Errors are returned as an explicit `ListStatus` in the result, not as exceptions.

## Concurrency model

* The server is **single-threaded asyncio** integrated with the KJ event loop of Cap'n Proto
  (`capnp.run`). Blackboard methods never `await` between reading and writing the dict, so each
  operation is atomic with respect to other clients. A `threading.Lock` is added anyway to document
  the intent and protect against future multi-threaded use.
* Directory reads are blocking file-system calls, so they run in `asyncio.to_thread()` and do not
  stall other clients.
* The client reads keyboard input with `asyncio.to_thread(input, ...)` so the event loop (and the
  connection) stays alive while waiting for the user.

## Failure handling

| Situation | Behavior |
|-----------|----------|
| Server not running | `ConnectionFailed` with a hint; CLI exits with code 1 |
| Server stops during a session | next call raises `RpcError("connection to the server was lost")`; the shell prints an error |
| Missing key | explicit `missing` branch of the `Lookup` union, never an empty string |
| Empty key | server raises an error that reaches the client as `RpcError` |
| Bad directory / forbidden path | `ListStatus` value plus human-readable message |

## Deployment

Locally: three terminals (server, client A, client B). Across machines: start the server with
`--host 0.0.0.0` and point clients to its IP with `--host`. There is no encryption or
authentication in this demo (see "Known limitations" in `CAPNPROTO_EXPLAINED.md`).
