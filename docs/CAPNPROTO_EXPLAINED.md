# Cap'n Proto Explained

## 1. RPC fundamentals

A **Remote Procedure Call** lets a program call a function that runs in *another process or on another
machine* as if it were a local call:

```python
result = board.put("topic", "x")      # looks local ...
# ... but the work happens on the server
```

To make that work, an RPC system provides:

1. **An interface definition** (IDL): a language-neutral description of the methods and data types.
2. **A client stub**: looks like the remote object; turns a call into a message.
3. **Marshalling / serialization**: converts arguments and results to bytes and back.
4. **A transport**: moves the bytes (here: TCP).
5. **A server skeleton / dispatcher**: receives the message and invokes the real implementation.

From the lecture's point of view this is the *request-reply* pattern with *access transparency*.
But the network is not a function call: it can be slow, messages can be lost, and the server can crash.
Good RPC frameworks make the call convenient **without hiding failures**. In this project failures
surface as exceptions (`ConnectionFailed`, `RpcError`).

## 2. What is Cap'n Proto?

Cap'n Proto is a data-serialization format *and* an RPC system, designed by Kenton Varda (previously
the author of Protocol Buffers v2). Its main ideas:

| Idea | Meaning |
|------|---------|
| **No encoding step** | The wire format is laid out like in-memory structs (with relative pointers). Reading a field means reading bytes at an offset. There is no "parse" phase that builds new objects. |
| **Schema-driven** | `.capnp` files define structs, enums, unions and interfaces. Fields have ordinal numbers (`@0`, `@1`) that define evolution rules. |
| **Capability-based RPC** | Remote objects are *capabilities*: references you can pass around and call. A server can return a new capability (we return `Blackboard` and `FileBrowser` objects from the root service). |
| **Promise pipelining** | You can call a method on the *not yet received* result of a previous call. The server chains the calls and the client avoids waiting for a round trip. |
| **Transport-agnostic** | The RPC protocol runs over any reliable byte stream; here a TCP socket. |

**pycapnp** is the Python binding. It wraps the C++ Cap'n Proto library (the Cython extension
`capnp.lib.capnp`), so serialization and the RPC protocol machinery are implemented in C++; Python is
the convenient layer on top. This project uses pycapnp 2.2.4.

## 3. Interface definition: `schema/blackboard.capnp`

```capnp
interface DemoService {
  blackboard @0 () -> (board :Blackboard);
  files      @1 () -> (browser :FileBrowser);
}

interface Blackboard {
  put    @0 (key :Text, value :Text) -> (status :PutStatus);
  get    @1 (key :Text)              -> (lookup :Lookup);
  remove @2 (key :Text)              -> (removed :Bool);
  list   @3 ()                       -> (entries :List(Entry));
}

struct Lookup {
  union {
    found   @0 :Text;
    missing @1 :Void;
  }
}
```

Reading `put @0 (key :Text, value :Text) -> (status :PutStatus)`:

* `put` is the method name; `@0` its permanent ordinal number (method id).
* The parentheses before `->` are the **parameters**; after `->` the **results**. Both are
  automatically-generated structs.
* `Lookup` is a **union**: exactly one of `found` or `missing` is set. This is how a missing key is
  kept distinct from an empty string value.
* `DemoService` is the **bootstrap interface**: the first capability a client receives. From it the
  client asks for the two sub-services, and everything uses one TCP connection.

## 4. Serialization

When the client calls `board.put("topic", "x")`, pycapnp

1. builds a *parameters struct* `{key: "topic", value: "x"}` in a Cap'n Proto message,
2. wraps it in an RPC `Call` message (target capability, interface id, method number),
3. writes the message segments to the TCP stream.

On the server the bytes are read into a message and the struct is accessed **in place**; the Python
method receives `key` and `value` as ordinary strings. The results travel back in a `Return`
message in the same manner.

A message is a sequence of *segments* preceded by a small header giving the segment count and sizes.
An optional "packed" encoding compresses runs of zero bytes. (RPC over a stream uses the standard
framing; you don't deal with that in application code.)

## 5. Schema loading in Python (no code-generation step)

Protocol Buffers and gRPC normally require a **compile step** (`protoc`) that generates Python files.
With pycapnp this is **not necessary**:

```python
import capnp
schema = capnp.load("schema/blackboard.capnp")   # parsed at run time
schema.Blackboard            # interface class
schema.Blackboard.Server     # base class for implementations
schema.Lookup.new_message()  # struct builder
```

`capnp.load()` calls the Cap'n Proto schema parser and builds the Python classes dynamically. (Cap'n
Proto in C++ or Rust does generate code with the `capnp compile` tool; pycapnp simply doesn't need it.)
In this repository `protocol.load_schema()` calls `capnp.load()` once and caches the result.
Trade-off: a mistake in the schema shows up at start-up (run time), not at a build step.

## 6. Asynchronous RPC, promises and event loops

In pycapnp 2.x every RPC runs on **asyncio** *and* the **KJ event loop** of the C++ library. The helper
`capnp.run(coro)` (or `async with capnp.kj_loop():`) connects the two:

```python
asyncio.run(capnp.run(main()))
```

A method call returns a **promise** immediately; you `await` it when you need the value:

```python
p1 = board.put("a", "1")      # request is sent, nothing is awaited yet
p2 = board.put("b", "2")      # second request, still not blocked
r1 = await p1
r2 = await p2
```

The `async-demo` command measures this against a server that delays each blackboard call by 0.5 s
(`--latency 0.5`). Result of an actual run on the development machine:

```
Sequential: 3 puts, each awaited before the next -> 1.55s
Concurrent: 3 requests sent in 0.1 ms, none answered yet (client is NOT blocked)
Concurrent: all 3 replies received after       -> 0.52s
```

**Promise pipelining** is visible in `client.py`:

```python
service = client.bootstrap().cast_as(schema.DemoService)
board = service.blackboard().board     # a call on the promised result - no await in between
```

The client already holds a usable `board` reference before the first reply arrives.

Important pycapnp 2.x rules used in the server: methods are `async def`; parameters arrive as keyword
arguments with the schema's names; results are returned or written to `_context.results`; blocking
code (like `input()` on the client or `Path.iterdir()` on the server) goes to a thread with
`asyncio.to_thread()` so the event loop keeps running.

## 7. Network failures

RPC cannot hide these:

| Failure | What this project does |
|---------|------------------------|
| Server is down at connect time | `RemoteDemo.connect` raises `ConnectionFailed` with a hint |
| Connection breaks while a call is pending | the pending promise raises `KjException` (type *disconnected*), converted to `RpcError("connection to the server was lost")` |
| Server code raises an exception | arrives at the client as a remote `KjException`; shown as `Error: ...` |
| Request lost / duplicated | TCP guarantees ordered delivery inside one connection; after a break there is **no automatic retry**, because retrying `put` is not always safe (compare *at-most-once* vs *at-least-once* semantics) |

Because `put` is idempotent for a given key and value, a manual retry is harmless here; `remove`
reports whether the key existed, which makes duplicate effects visible.

## 8. Advantages and limitations

**Advantages**

* Very cheap serialization (no parse step; zero-copy reads).
* Rich, evolvable schemas (unions, generics, default values, ordinals).
* Capabilities and promise pipelining give an elegant object-oriented distributed model.
* One protocol for in-process, IPC and network use; any byte stream works as a transport.
* In pycapnp, no code generation step.

**Limitations (general)**

* Smaller ecosystem and fewer language implementations than gRPC/Protobuf.
* The implemented RPC level covers two-party connections; features such as three-party hand-off of
  capabilities between machines are not implemented in the mainstream libraries.
* No built-in authentication or encryption: add TLS (pycapnp can wrap the asyncio connection with an
  `ssl` context) or run it inside a trusted network or tunnel.
* No load-balancer or browser-friendliness story comparable to HTTP-based RPC.

**Limitations (this demo)**

* No authentication, no TLS, no persistence (the blackboard is lost when the server stops).
* One central server is a single point of failure and a bottleneck.
* Ctrl+C shutdown and the Linux/macOS platforms were not tested during development (only Windows).

## 9. Meaningful differences compared with gRPC

| Aspect | Cap'n Proto | gRPC |
|--------|-------------|------|
| Message format | Cap'n Proto (in-memory-like layout, zero-copy reads) | Protocol Buffers (encode/decode step) |
| Transport | Any byte stream; own RPC framing | HTTP/2 |
| Interface style | Capability objects can be returned and passed around; promise pipelining | Services with methods on a stub; no object references |
| Streaming | Not the central concept (calls are request/response; streaming patterns are built differently) | First-class unary, server, client and bidirectional streaming |
| Ecosystem | Smaller | Very large (many languages, proxies, tooling, observability) |
| Python usage | `capnp.load()` at run time | usually generate code with `protoc` / grpcio-tools |
| Security | Bring your own TLS/auth | TLS and auth integrated |

Rule of thumb: gRPC is the safe choice for broad, polyglot microservice environments; Cap'n Proto
shines when low serialization overhead, object-capability design or latency (pipelining) matter.

## 10. Further reading

* Cap'n Proto RPC protocol: <https://capnproto.org/rpc.html>
* Cap'n Proto schema language: <https://capnproto.org/language.html>
* pycapnp quickstart: <https://capnproto.github.io/pycapnp/quickstart.html>
* pycapnp repository: <https://github.com/capnproto/pycapnp>
