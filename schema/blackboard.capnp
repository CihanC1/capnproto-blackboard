@0xb7e1d3c9a4f25e68;
# Unique 64-bit file ID (must have the highest bit set).

# ---------------------------------------------------------------------------
# Root capability ("bootstrap interface")
# ---------------------------------------------------------------------------
# A client connects to the server and receives exactly one capability: a
# DemoService. From there it asks for the two sub-services. Both sub-services
# live in ONE server process and are reached over ONE TCP connection.

interface DemoService {
  blackboard @0 () -> (board :Blackboard);
  files      @1 () -> (browser :FileBrowser);
}

# ---------------------------------------------------------------------------
# Blackboard: a shared key/value store owned by the server
# ---------------------------------------------------------------------------

enum PutStatus {
  created @0;   # the key did not exist before
  updated @1;   # the key existed and its value was replaced
}

struct Lookup {
  # A lookup either finds a value or reports that the key is missing.
  # An empty string is a perfectly valid value, so "missing" must be a
  # separate case - that is what this union is for.
  union {
    found   @0 :Text;
    missing @1 :Void;
  }
}

struct Entry {
  key   @0 :Text;
  value @1 :Text;
}

interface Blackboard {
  put    @0 (key :Text, value :Text) -> (status :PutStatus);
  get    @1 (key :Text)              -> (lookup :Lookup);
  remove @2 (key :Text)              -> (removed :Bool);
  list   @3 ()                       -> (entries :List(Entry));
}

# ---------------------------------------------------------------------------
# FileBrowser: read-only directory listing on the server's file system
# ---------------------------------------------------------------------------

enum EntryType {
  file      @0;
  directory @1;
  symlink   @2;
  other     @3;
}

enum ListStatus {
  ok               @0;
  notFound         @1;   # path does not exist
  notADirectory    @2;   # path exists but is a file
  accessDenied     @3;   # outside the shared root (traversal, absolute path, symlink escape)
  permissionDenied @4;   # the operating system refused to read the directory
}

struct DirEntry {
  name @0 :Text;
  type @1 :EntryType;
  size @2 :UInt64;       # size in bytes (0 for directories)
}

struct DirListing {
  status  @0 :ListStatus;
  message @1 :Text;              # human-readable explanation when status != ok
  entries @2 :List(DirEntry);    # empty unless status == ok
}

interface FileBrowser {
  listDirectory @0 (path :Text) -> (listing :DirListing);
}
