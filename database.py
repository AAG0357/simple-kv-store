"""Persistent key-value store with a list-based index."""

import json
import math
import os
import sys
import time


class Database:
    def __init__(self, filename="data.db"):
        self.filename = filename
        # Each entry contains: key, value, expiration timestamp.
        self.entries = []
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
        with open(self.filename, "a", encoding="utf-8") as log:
            log.write(json.dumps(record) + "\n")
            log.flush()
            os.fsync(log.fileno())

    def apply_record(self, record):
        # Support SET records from our first version.
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

    def recover(self):
        try:
            with open(self.filename, "r", encoding="utf-8") as log:
                for line in log:
                    self.apply_record(json.loads(line))
        except FileNotFoundError:
            pass

    def set(self, key, value):
        self.append_record(["SET", key, value])
        self.update_index(key, value)

    def get(self, key):
        index = self.live_index(key)
        if index == -1:
            return "(nil)"
        return self.entries[index][1]

    def delete(self, key):
        index = self.live_index(key)
        if index == -1:
            return 0
        self.append_record(["DEL", key, None])
        self.entries.pop(index)
        return 1

    def exists(self, key):
        return int(self.live_index(key) != -1)

    def mset(self, pairs):
        self.append_record(["MSET", pairs, None])
        for key, value in pairs:
            self.update_index(key, value)

    def mget(self, keys):
        return [self.get(key) for key in keys]

    def expire(self, key, seconds):
        index = self.live_index(key)
        if index == -1:
            return 0
        # Store an absolute time so expiration survives restarts.
        deadline = time.time() + seconds
        self.append_record(["EXPIRE", key, deadline])
        self.entries[index][2] = deadline
        return 1

    def ttl(self, key):
        index = self.live_index(key)
        if index == -1:
            return -2
        deadline = self.entries[index][2]
        if deadline is None:
            return -1
        return max(0, math.ceil(deadline - time.time()))


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
        elif command == "SET" and len(args) >= 2:
            database.set(args[0], " ".join(args[1:]))
            print("OK", flush=True)
        elif command == "GET" and len(args) == 1:
            print(database.get(args[0]), flush=True)
        elif command == "DEL" and len(args) == 1:
            print(database.delete(args[0]), flush=True)
        elif command == "EXISTS" and len(args) == 1:
            print(database.exists(args[0]), flush=True)
        elif command == "MSET" and len(args) >= 2 and len(args) % 2 == 0:
            pairs = []
            for index in range(0, len(args), 2):
                pairs.append([args[index], args[index + 1]])
            database.mset(pairs)
            print("OK", flush=True)
        elif command == "MGET" and args:
            print(" ".join(database.mget(args)), flush=True)
        elif command == "EXPIRE" and len(args) == 2:
            try:
                seconds = int(args[1])
            except ValueError:
                print("ERROR: Seconds must be an integer", flush=True)
                continue
            print(database.expire(args[0], seconds), flush=True)
        elif command == "TTL" and len(args) == 1:
            print(database.ttl(args[0]), flush=True)
        else:
            print("ERROR: Invalid command", flush=True)


if __name__ == "__main__":
    main()