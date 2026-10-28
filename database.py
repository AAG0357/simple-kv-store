"""Append-only database with a custom list-based index."""

import copy
import json
import math
import os
import sys
import time


class Database:
    def __init__(self, filename="data.db"):
        self.filename = filename
        # Entries: [key, value, expiration timestamp, value type].
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

    def update_index(self, key, value, deadline=None, kind="string"):
        entry = [key, value, deadline, kind]
        index = self.find(key)
        if index == -1:
            self.entries.append(entry)
        else:
            self.entries[index] = entry

    def append_record(self, record):
        with open(self.filename, "a", encoding="utf-8") as log:
            log.write(json.dumps(record) + "\n")
            log.flush()
            os.fsync(log.fileno())

    def apply_record(self, record):
        # Read records created by our earlier versions.
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
            self.update_index(record[1], record[2], record[3])
        elif record[0] == "VALUE":
            self.update_index(record[1], record[2], record[3], record[4])
        elif record[0] == "TRANSACTION":
            for operation in record[1]:
                self.apply_record(operation)
        elif record[0] == "FLUSHDB":
            self.entries = []

    def recover(self):
        try:
            with open(self.filename, "r", encoding="utf-8") as log:
                for line in log:
                    self.apply_record(json.loads(line))
        except FileNotFoundError:
            pass

    def write(self, record):
        if self.snapshot is not None:
            self.pending.append(copy.deepcopy(record))
        else:
            self.append_record(record)
        self.apply_record(record)

    def typed_index(self, key, kind):
        index = self.live_index(key)
        if index != -1 and self.entries[index][3] != kind:
            raise ValueError("Wrong value type")
        return index

    def set(self, key, value):
        self.write(["SET", key, value])

    def get(self, key):
        index = self.typed_index(key, "string")
        return "(nil)" if index == -1 else self.entries[index][1]

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
        self.write(["EXPIRE", key, time.time() + seconds])
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
        index = self.typed_index(key, "string")
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

    def hset(self, name, field, value):
        index = self.typed_index(name, "hash")
        fields = []
        deadline = None
        if index != -1:
            fields = copy.deepcopy(self.entries[index][1])
            deadline = self.entries[index][2]

        added = 1
        for pair in fields:
            if pair[0] == field:
                pair[1] = value
                added = 0
                break
        else:
            fields.append([field, value])

        self.write(["VALUE", name, fields, deadline, "hash"])
        return added

    def hget(self, name, field):
        index = self.typed_index(name, "hash")
        if index != -1:
            for pair in self.entries[index][1]:
                if pair[0] == field:
                    return pair[1]
        return "(nil)"

    def hgetall(self, name):
        index = self.typed_index(name, "hash")
        if index == -1:
            return []
        result = []
        for field, value in self.entries[index][1]:
            result.extend([field, value])
        return result

    def push(self, key, value, left):
        index = self.typed_index(key, "list")
        values = []
        deadline = None
        if index != -1:
            values = self.entries[index][1].copy()
            deadline = self.entries[index][2]
        if left:
            values.insert(0, value)
        else:
            values.append(value)
        self.write(["VALUE", key, values, deadline, "list"])
        return len(values)

    def lrange(self, key, start, stop):
        index = self.typed_index(key, "list")
        if index == -1:
            return []
        values = self.entries[index][1]
        length = len(values)
        if start < 0:
            start += length
        if stop < 0:
            stop += length
        start = max(0, start)
        stop = min(length - 1, stop)
        if start > stop:
            return []
        return values[start : stop + 1]

    def flushdb(self):
        # A reset record preserves append-only storage.
        self.write(["FLUSHDB", None, None])
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
        return database.expire(args[0], int(args[1]))
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
    if command == "HSET" and len(args) == 3:
        return database.hset(args[0], args[1], args[2])
    if command == "HGET" and len(args) == 2:
        return database.hget(args[0], args[1])
    if command == "HGETALL" and len(args) == 1:
        return " ".join(database.hgetall(args[0]))
    if command in ("LPUSH", "RPUSH") and len(args) == 2:
        return database.push(args[0], args[1], command == "LPUSH")
    if command == "LRANGE" and len(args) == 3:
        return " ".join(database.lrange(args[0], int(args[1]), int(args[2])))
    if command == "FLUSHDB" and not args:
        return database.flushdb()
    raise ValueError("Invalid command")


def main():
    database = Database()
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

    if database.snapshot is not None:
        database.abort()


if __name__ == "__main__":
    main()
