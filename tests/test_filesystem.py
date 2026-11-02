"""Unit tests for the sandboxed directory lister (no networking)."""

import os
import sys

import pytest

from capnproto_demo.filesystem import DirectoryLister, EntryType, ListStatus


@pytest.fixture
def root(tmp_path):
    shared = tmp_path / "shared"
    (shared / "docs").mkdir(parents=True)
    (shared / "empty").mkdir()
    (shared / "a.txt").write_text("hello")
    (shared / "docs" / "n.md").write_text("x")
    (tmp_path / "secret.txt").write_text("top secret")  # outside the root
    return shared


def test_lists_files_and_directories(root):
    listing = DirectoryLister(root).list(".")
    assert listing.status is ListStatus.OK
    assert [(e.name, e.type) for e in listing.entries] == [
        ("docs", EntryType.DIRECTORY),
        ("empty", EntryType.DIRECTORY),
        ("a.txt", EntryType.FILE),
    ]
    assert listing.entries[2].size == 5


def test_subdirectory(root):
    listing = DirectoryLister(root).list("docs")
    assert [e.name for e in listing.entries] == ["n.md"]


def test_empty_directory(root):
    listing = DirectoryLister(root).list("empty")
    assert listing.status is ListStatus.OK
    assert listing.entries == ()


def test_nonexistent_directory(root):
    assert DirectoryLister(root).list("nope").status is ListStatus.NOT_FOUND


def test_file_is_not_a_directory(root):
    assert DirectoryLister(root).list("a.txt").status is ListStatus.NOT_A_DIRECTORY


@pytest.mark.parametrize(
    "path",
    ["..", "../secret.txt", "docs/../..", "/etc", "C:\\Windows", "\\\\server\\share",
     "C:", "/", "\\", "bad\x00name"],
)
def test_escape_attempts_are_denied(root, path):
    assert DirectoryLister(root).list(path).status is ListStatus.ACCESS_DENIED


def test_dotdot_that_stays_inside_is_allowed(root):
    assert DirectoryLister(root).list("docs/..").status is ListStatus.OK


def test_symlink_escape_is_denied(root, tmp_path):
    link = root / "evil"
    try:
        os.symlink(tmp_path, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this system")
    lister = DirectoryLister(root)
    assert lister.list("evil").status is ListStatus.ACCESS_DENIED
    # The link itself is shown as a symlink, but never followed.
    types = {e.name: e.type for e in lister.list(".").entries}
    assert types["evil"] is EntryType.SYMLINK


@pytest.mark.skipif(sys.platform == "win32", reason="chmod semantics differ on Windows")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores permissions")
def test_permission_error(root):
    locked = root / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        assert DirectoryLister(root).list("locked").status is ListStatus.PERMISSION_DENIED
    finally:
        locked.chmod(0o755)


def test_invalid_root_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        DirectoryLister(tmp_path / "missing")
