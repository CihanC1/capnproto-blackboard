"""Safe, read-only directory listing confined to a configured root directory.

The server calls ``DirectoryLister.list()`` when a client invokes the remote
``listDirectory`` method. Everything a client asks for is resolved *inside* the
root: absolute paths, ``..`` traversal and symlinks that point outside are
rejected before the file system is read.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath


class EntryType(StrEnum):
    """Values match the ``EntryType`` enumerants in the schema."""

    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    OTHER = "other"


class ListStatus(StrEnum):
    """Values match the ``ListStatus`` enumerants in the schema."""

    OK = "ok"
    NOT_FOUND = "notFound"
    NOT_A_DIRECTORY = "notADirectory"
    ACCESS_DENIED = "accessDenied"
    PERMISSION_DENIED = "permissionDenied"


@dataclass(frozen=True)
class DirEntry:
    name: str
    type: EntryType
    size: int


@dataclass(frozen=True)
class DirListing:
    status: ListStatus
    message: str = ""
    entries: tuple[DirEntry, ...] = ()


def _looks_absolute(requested: str) -> bool:
    """True for POSIX-absolute, Windows-absolute, drive-relative and UNC paths."""
    return (
        requested.startswith(("/", "\\"))
        or PureWindowsPath(requested).drive != ""
        or PurePosixPath(requested).is_absolute()
    )


class DirectoryLister:
    """Lists directories below ``root`` and nowhere else."""

    def __init__(self, root: str | Path) -> None:
        resolved = Path(root).resolve(strict=True)
        if not resolved.is_dir():
            raise NotADirectoryError(f"Shared root is not a directory: {resolved}")
        self.root = resolved

    def _resolve_inside_root(self, requested: str) -> Path | None:
        """Resolve ``requested`` against the root; None if it escapes the root.

        ``Path.resolve()`` collapses ``..`` and follows symlinks, so comparing
        the *resolved* path with the root catches both path traversal and
        symlinks that point outside the shared directory.
        """
        if "\x00" in requested or _looks_absolute(requested):
            return None
        try:
            candidate = (self.root / requested).resolve(strict=False)
        except (OSError, RuntimeError, ValueError):  # e.g. symlink loops
            return None
        return candidate if candidate.is_relative_to(self.root) else None

    def list(self, requested: str = ".") -> DirListing:
        """Return the contents of ``requested`` (relative to the root)."""
        target = self._resolve_inside_root(requested)
        if target is None:
            return DirListing(
                ListStatus.ACCESS_DENIED,
                f"access to '{requested}' is not allowed "
                "(only paths inside the shared directory are permitted)",
            )
        try:
            if not target.exists():
                return DirListing(ListStatus.NOT_FOUND, f"'{requested}' does not exist")
            if not target.is_dir():
                return DirListing(
                    ListStatus.NOT_A_DIRECTORY, f"'{requested}' is not a directory"
                )
            children = list(target.iterdir())
        except FileNotFoundError:
            return DirListing(ListStatus.NOT_FOUND, f"'{requested}' does not exist")
        except PermissionError:
            return DirListing(
                ListStatus.PERMISSION_DENIED,
                f"permission denied while reading '{requested}'",
            )
        except OSError:
            return DirListing(
                ListStatus.PERMISSION_DENIED, f"could not read '{requested}'"
            )

        entries = [self._describe(child) for child in children]
        entries.sort(key=lambda e: (e.type is not EntryType.DIRECTORY, e.name.lower()))
        return DirListing(ListStatus.OK, "", tuple(entries))

    @staticmethod
    def _describe(child: Path) -> DirEntry:
        """Classify one child without following symlinks out of the root."""
        try:
            if child.is_symlink():
                return DirEntry(child.name, EntryType.SYMLINK, 0)
            if child.is_dir():
                return DirEntry(child.name, EntryType.DIRECTORY, 0)
            if child.is_file():
                return DirEntry(child.name, EntryType.FILE, child.stat().st_size)
        except OSError:
            pass
        return DirEntry(child.name, EntryType.OTHER, 0)
