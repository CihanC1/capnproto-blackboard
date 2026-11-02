"""Schema loading and default paths shared by the server and the client.

pycapnp loads ``.capnp`` files *dynamically* at run time with ``capnp.load()``.
There is no separate code-generation step: the returned module object already
contains Python classes for every struct, enum and interface in the schema.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

import capnp

# Repository layout: <root>/src/capnproto_demo/protocol.py -> parents[2] == <root>
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "schema" / "blackboard.capnp"
DEFAULT_SHARED_ROOT = PROJECT_ROOT / "demo" / "shared-files"
DEFAULT_PORT = 60000
SCHEMA_ENV_VAR = "CAPNP_DEMO_SCHEMA"


def schema_path() -> Path:
    """Return the schema file location (override with CAPNP_DEMO_SCHEMA)."""
    override = os.environ.get(SCHEMA_ENV_VAR)
    return Path(override) if override else DEFAULT_SCHEMA_PATH


@functools.cache
def load_schema():
    """Load ``blackboard.capnp`` and return the dynamic schema module."""
    path = schema_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"Cap'n Proto schema not found: {path}. "
            f"Run from the repository (pip install -e .) or set {SCHEMA_ENV_VAR}."
        )
    return capnp.load(str(path))
