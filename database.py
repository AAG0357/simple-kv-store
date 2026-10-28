"""Persistent key-value store using a list-based index."""

import copy
import json
import math
import os
import sys
import time


class Database:
    def __init__(self, filename="data.db"):
        self.filename = filename
        # Entries contain: key, value, expiration timestamp.
        self.entries = []
        self.snapshot = None
        self.pending = []
        self.recover()

    def find(self, key):
        for index, entry in enumerate(self.entries):
            if entry[0] == key:
                return index
        return -1

    def live_index(self, key):
        index = self.find(key)
        if index != -1:
            deadline = self.entries[index][2]
            if deadline is not None and time.time() >= deadline:
                self.entries.pop(index)
                return -1
        return index

    def update_index(self, key, value):
        index = self.find(key)
        entry = [key, value, None]
        if index == -1:
            self.entries.append(entry)
        else:
            self.entries[index] = entry

    def append_record(self, record):
        """Immediately flush a log record to disk."""
        with open(self.filename, "a", encoding="utf-8") as log:
            log.write(json.dumps(record) + "\n")
            log.flush()
            os.fsync(log.fileno())

    def apply_record(self, record):
        # Support the records saved by our earlier versions.
        if len(record) == 2:
            self.update_index(record[0], record[1])
        elif record[0] == "SET":
            self.update_index(record[1], record[2])
        elif record[0] == "MSET":
            for key, value in record[1]:
                self.update_index(key, value)
        elif record[0] == "DEL":
            index = self.find(record[1])
            if index != -1:
                self.entries.pop(index)
        elif record[0] == "EXPIRE":
            index = self.find(record[1])
            if index != -1:
                self.entries[index][2] = record[2]
        elif record[0] == "COUNTER":
            self.update_index(record[1], record[2])
            index = self.find(record[1])
            self.entries[index][2] = record[3]
        elif record[0] == "TRANSACTION":
            for operation in record[1]:
                self.apply_record(operation)

    def recover(self):
        """Replay the log to rebuild the in-memory index."""
        try:
            with open(self.filename, "r", encoding="utf-8") as log:
                for line in log:
                    self.apply_record(json.loads(line))
        except FileNotFoundError:
            pass

    def write(self, record):
        """Queue transaction writes; persist other writes immediately."""
        if self.snapshot is not None:
            self.pending.append(record)
        else:
            self.append_record(record)
        self.apply_record(record)

    def set(self, key, value):
        self.write(["SET", key, value])

    def get(self, key):
        index = self.live_index(key)
        if index == -1:
            return "(nil)"
        return self.entries[index][1]

    def delete(self, key):
        if self.live_index(key) == -1:
            return 0
        self.write(["DEL", key, None])
        return 1

    def exists(self, key):
        return int(self.live_index(key) != -1)

    def mset(self, pairs):
        self.write(["MSET", pairs, None])

    def mget(self, keys):
        return [self.get(key) for key in keys]

    def expire(self, key, seconds):
        if self.live_index(key) == -1:
            return 0
        deadline = time.time() + seconds
        self.write(["EXPIRE", key, deadline])
        return 1

    def ttl(self, key):
        index = self.live_index(key)
        if index == -1:
            return -2
        deadline = self.entries[index][2]
        if deadline is None:
            return -1
        return max(0, math.ceil(deadline - time.time()))

    def range_keys(self, start, end):
        keys = [entry[0] for entry in self.entries]
        result = []
        for key in keys:
            if self.live_index(key) != -1 and start <= key <= end:
                result.append(key)
        return sorted(result)

    def change_counter(self, key, amount):
        index = self.live_index(key)
        value = 0
        deadline = None

        if index != -1:
            try:
                value = int(self.entries[index][1])
            except ValueError:
                raise ValueError("Value must be an integer") from None
            deadline = self.entries[index][2]

        value += amount
        self.write(["COUNTER", key, str(value), deadline])
        return value

    def begin(self):
        if self.snapshot is not None:
            raise ValueError("A transaction is already active")
        self.snapshot = copy.deepcopy(self.entries)
        self.pending = []
        return "OK"

    def commit(self):
        if self.snapshot is None:
            raise ValueError("No active transaction")

        # Store the entire transaction in one log record.
        self.append_record(["TRANSACTION", self.pending, None])
        self.snapshot = None
        self.pending = []
        return "OK"

    def abort(self):
        if self.snapshot is None:
            raise ValueError("No active transaction")
        self.entries = self.snapshot
        self.snapshot = None
        self.pending = []
        return "OK"


def execute(database, command, args):
    if command == "SET" and len(args) >= 2:
        database.set(args[0], " ".join(args[1:]))
        return "OK"
    if command == "GET" and len(args) == 1:
        return database.get(args[0])
    if command == "DEL" and len(args) == 1:
        return database.delete(args[0])
    if command == "EXISTS" and len(args) == 1:
        return database.exists(args[0])
    if command == "MSET" and len(args) >= 2 and len(args) % 2 == 0:
        pairs = []
        for index in range(0, len(args), 2):
            pairs.append([args[index], args[index + 1]])
        database.mset(pairs)
        return "OK"
    if command == "MGET" and args:
        return " ".join(database.mget(args))
    if command == "EXPIRE" and len(args) == 2:
        try:
            seconds = int(args[1])
        except ValueError:
            raise ValueError("Seconds must be an integer") from None
        return database.expire(args[0], seconds)
    if command == "TTL" and len(args) == 1:
        return database.ttl(args[0])
    if command == "RANGE" and len(args) == 2:
        return " ".join(database.range_keys(args[0], args[1]))
    if command in ("INCR", "DECR") and len(args) == 1:
        amount = 1 if command == "INCR" else -1
        return database.change_counter(args[0], amount)
    if command == "BEGIN" and not args:
        return database.begin()
    if command == "COMMIT" and not args:
        return database.commit()
    if command == "ABORT" and not args:
        return database.abort()
    raise ValueError("Invalid command")


def main():
    database = Database()

    # No prompts: automated testers read only command results.
    for line in sys.stdin:
        parts = line.strip().split()
        if not parts:
            continue

        command = parts[0].upper()
        args = parts[1:]

        if command == "EXIT" and not args:
            break

        try:
            result = execute(database, command, args)
        except ValueError as error:
            print(f"ERROR: {error}", flush=True)
        else:
            print(result, flush=True)

    # Leaving without COMMIT discards the active transaction.
    if database.snapshot is not None:
        database.abort()


if __name__ == "__main__":
    main()