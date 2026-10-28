"""Persistent key-value store using a list-based index."""

import json
import os
import sys


class Database:
    def __init__(self, filename="data.db"):
        self.filename = filename
        self.entries = []
        self.recover()

    def find(self, key):
        """Find a key without using a dictionary."""
        for index, entry in enumerate(self.entries):
            if entry[0] == key:
                return index
        return -1

    def update_index(self, key, value):
        """Insert or replace a value in memory."""
        index = self.find(key)
        if index == -1:
            self.entries.append([key, value])
        else:
            self.entries[index][1] = value

    def append_record(self, record):
        """Append a record and immediately flush it to disk."""
        with open(self.filename, "a", encoding="utf-8") as log:
            log.write(json.dumps(record) + "\n")
            log.flush()
            os.fsync(log.fileno())

    def recover(self):
        """Replay saved writes to rebuild the index."""
        try:
            with open(self.filename, "r", encoding="utf-8") as log:
                for line in log:
                    record = json.loads(line)

                    # Support records saved by the first version.
                    if len(record) == 2:
                        self.update_index(record[0], record[1])
                    elif record[0] == "SET":
                        self.update_index(record[1], record[2])
                    elif record[0] == "DEL":
                        index = self.find(record[1])
                        if index != -1:
                            self.entries.pop(index)
                    elif record[0] == "MSET":
                        for key, value in record[1]:
                            self.update_index(key, value)
        except FileNotFoundError:
            pass

    def set(self, key, value):
        self.append_record(["SET", key, value])
        self.update_index(key, value)

    def get(self, key):
        index = self.find(key)
        if index == -1:
            return "(nil)"
        return self.entries[index][1]

    def delete(self, key):
        index = self.find(key)
        if index == -1:
            return 0

        self.append_record(["DEL", key, None])
        self.entries.pop(index)
        return 1

    def exists(self, key):
        return int(self.find(key) != -1)

    def mset(self, pairs):
        """Save all pairs together in one log record."""
        self.append_record(["MSET", pairs, None])
        for key, value in pairs:
            self.update_index(key, value)

    def mget(self, keys):
        return [self.get(key) for key in keys]


def main():
    database = Database()

    # Print only command results for automated testing.
    for line in sys.stdin:
        parts = line.strip().split()
        if not parts:
            continue

        command = parts[0].upper()
        arguments = parts[1:]

        if command == "EXIT" and not arguments:
            break

        elif command == "SET" and len(arguments) >= 2:
            database.set(arguments[0], " ".join(arguments[1:]))
            print("OK", flush=True)

        elif command == "GET" and len(arguments) == 1:
            print(database.get(arguments[0]), flush=True)

        elif command == "DEL" and len(arguments) == 1:
            print(database.delete(arguments[0]), flush=True)

        elif command == "EXISTS" and len(arguments) == 1:
            print(database.exists(arguments[0]), flush=True)

        elif (
            command == "MSET"
            and len(arguments) >= 2
            and len(arguments) % 2 == 0
        ):
            pairs = []
            for index in range(0, len(arguments), 2):
                pairs.append([arguments[index], arguments[index + 1]])
            database.mset(pairs)
            print("OK", flush=True)

        elif command == "MGET" and len(arguments) >= 1:
            values = database.mget(arguments)
            print(" ".join(values), flush=True)

        else:
            print("ERROR: Invalid command", flush=True)


if __name__ == "__main__":
    main()