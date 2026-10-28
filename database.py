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

                    # Read SET records from the earlier version.
                    if len(record) == 2:
                        self.update_index(record[0], record[1])
                    elif record[0] == "SET":
                        self.update_index(record[1], record[2])
                    elif record[0] == "DEL":
                        index = self.find(record[1])
                        if index != -1:
                            self.entries.pop(index)
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


def main():
    database = Database()

    # Read commands without printing prompts for automated testing.
    for line in sys.stdin:
        parts = line.strip().split(maxsplit=2)
        if not parts:
            continue

        command = parts[0].upper()

        if command == "EXIT" and len(parts) == 1:
            break
        elif command == "SET" and len(parts) == 3:
            database.set(parts[1], parts[2])
            print("OK", flush=True)
        elif command == "GET" and len(parts) == 2:
            print(database.get(parts[1]), flush=True)
        elif command == "DEL" and len(parts) == 2:
            print(database.delete(parts[1]), flush=True)
        elif command == "EXISTS" and len(parts) == 2:
            print(database.exists(parts[1]), flush=True)
        else:
            print("ERROR: Invalid command", flush=True)


if __name__ == "__main__":
    main()