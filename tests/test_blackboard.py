"""Unit tests for the in-memory blackboard (no networking)."""

import threading

from capnproto_demo.blackboard import Blackboard


def test_put_creates_then_updates():
    board = Blackboard()
    assert board.put("topic", "A") is True
    assert board.put("topic", "B") is False
    assert board.get("topic") == "B"


def test_missing_key_is_none_not_empty_string():
    board = Blackboard()
    assert board.get("nope") is None
    board.put("empty", "")
    assert board.get("empty") == ""


def test_remove():
    board = Blackboard()
    board.put("k", "v")
    assert board.remove("k") is True
    assert board.remove("k") is False
    assert board.get("k") is None


def test_items_sorted_snapshot():
    board = Blackboard()
    board.put("b", "2")
    board.put("a", "1")
    assert board.items() == [("a", "1"), ("b", "2")]
    assert len(board) == 2


def test_concurrent_writers_do_not_lose_updates():
    board = Blackboard()

    def worker(n: int) -> None:
        for i in range(200):
            board.put(f"k{n}-{i}", str(i))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(board) == 8 * 200
