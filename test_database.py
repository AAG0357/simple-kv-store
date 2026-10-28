"""Tests for database commands, persistence, and transactions."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import Database


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.filename = os.path.join(self.temp.name, "data.db")
        self.db = Database(self.filename)

    def restart(self):
        self.db = Database(self.filename)

    def test_set_get_and_last_write_wins(self):
        self.db.set("name", "first")
        self.db.set("name", "second")
        self.restart()
        self.assertEqual(self.db.get("name"), "second")
        self.assertEqual(len(self.db.entries), 1)
        self.assertEqual(self.db.get("missing"), "")

    def test_delete_and_exists(self):
        self.db.set("key", "value")
        self.assertEqual(self.db.exists("key"), 1)
        self.assertEqual(self.db.delete("key"), 1)
        self.assertEqual(self.db.delete("key"), 0)
        self.restart()
        self.assertEqual(self.db.exists("key"), 0)

    def test_multiple_operations(self):
        self.db.mset([["a", "one"], ["b", "two"]])
        self.restart()
        self.assertEqual(
            self.db.mget(["b", "missing", "a"]),
            ["two", "", "one"],
        )

    def test_expiration_survives_restart(self):
        with patch("database.time.time", return_value=1000):
            self.db.set("key", "value")
            self.assertEqual(self.db.ttl("key"), -1)
            self.assertEqual(self.db.expire("key", 10), 1)

        with patch("database.time.time", return_value=1004):
            self.restart()
            self.assertEqual(self.db.ttl("key"), 6)
            self.assertEqual(self.db.get("key"), "value")

        with patch("database.time.time", return_value=1010):
            self.assertEqual(self.db.get("key"), "")
            self.assertEqual(self.db.ttl("key"), -2)

    def test_range_is_sorted_and_inclusive(self):
        self.db.mset(
            [
                ["cherry", "3"],
                ["apple", "1"],
                ["banana", "2"],
            ]
        )
        self.assertEqual(
            self.db.range_keys("apple", "banana"),
            ["apple", "banana"],
        )

    def test_counters_and_invalid_values(self):
        self.assertEqual(self.db.change_counter("count", 1), 1)
        self.assertEqual(self.db.change_counter("count", -1), 0)
        self.db.set("text", "hello")
        with self.assertRaises(ValueError):
            self.db.change_counter("text", 1)
        self.restart()
        self.assertEqual(self.db.get("count"), "0")

    def test_commit_is_persistent(self):
        self.db.begin()
        self.db.set("a", "one")
        self.db.set("b", "two")
        self.assertEqual(Database(self.filename).exists("a"), 0)
        self.db.commit()
        self.restart()
        self.assertEqual(self.db.mget(["a", "b"]), ["one", "two"])

    def test_abort_restores_previous_values(self):
        self.db.set("key", "old")
        self.db.begin()
        self.db.set("key", "new")
        self.db.set("extra", "value")
        self.db.abort()
        self.assertEqual(self.db.get("key"), "old")
        self.assertEqual(self.db.exists("extra"), 0)
        self.restart()
        self.assertEqual(self.db.get("key"), "old")

    def test_uncommitted_changes_are_not_recovered(self):
        self.db.begin()
        self.db.set("temporary", "value")
        self.restart()
        self.assertEqual(self.db.exists("temporary"), 0)

    def test_hash_fields_and_persistence(self):
        self.assertEqual(self.db.hset("student", "major", "CS"), 1)
        self.assertEqual(self.db.hset("student", "major", "Math"), 0)
        self.restart()
        self.assertEqual(self.db.hget("student", "major"), "Math")
        self.assertEqual(self.db.hgetall("student"), ["major", "Math"])
        self.assertEqual(self.db.hget("student", "missing"), "")

    def test_lists_and_negative_indexes(self):
        self.db.push("tasks", "study", True)
        self.db.push("tasks", "homework", False)
        self.db.push("tasks", "read", True)
        self.restart()
        self.assertEqual(
            self.db.lrange("tasks", 0, -1),
            ["read", "study", "homework"],
        )
        self.assertEqual(self.db.lrange("tasks", -2, -1), ["study", "homework"])
        self.assertEqual(self.db.lrange("tasks", 5, 10), [])

    def test_wrong_type_does_not_modify_data(self):
        self.db.set("key", "text")
        with self.assertRaises(ValueError):
            self.db.push("key", "item", True)
        self.assertEqual(self.db.get("key"), "text")

    def test_flushdb_and_restart(self):
        self.db.set("key", "value")
        self.db.hset("hash", "field", "value")
        self.db.flushdb()
        self.restart()
        self.assertEqual(self.db.entries, [])

    def test_transaction_abort_restores_hash_and_list(self):
        self.db.hset("hash", "field", "old")
        self.db.push("list", "old", False)
        self.db.begin()
        self.db.hset("hash", "field", "new")
        self.db.push("list", "new", False)
        self.db.abort()
        self.assertEqual(self.db.hget("hash", "field"), "old")
        self.assertEqual(self.db.lrange("list", 0, -1), ["old"])

    def test_cli_accepts_piped_commands(self):
        script = Path(__file__).resolve().with_name("database.py")
        result = subprocess.run(
            [sys.executable, str(script)],
            input="SET key value\nGET key\nEXIT\n",
            capture_output=True,
            text=True,
            cwd=self.temp.name,
            timeout=10,
            check=True,
        )
        self.assertEqual(result.stdout.splitlines(), ["OK", "value"])
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
