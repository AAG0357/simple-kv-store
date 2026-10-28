"""A persistent key-value store with a list-based index."""

import json
import os
import sys


class Database:
    def __init__(self, filename="data.db"):
        self.filename = filename
        self.entries = []
        self.recover()

    def find(self, key):
        for index, entry in enumerate(self.entries):
            if entry[0] == key:
                return index
        return -1

    def update_index(self, key, value):
        index = self.find(key)
        if index == -1:
            self.entries.append([key, value])
        else:
            self.entries[index][1] = value

    def recover(self):
        try:
            with open(self.filename, "r", encoding="utf-8") as log:
                for line in log:
                    key, value = json.loads(line)
                    self.update_index(key, value)
        except FileNotFoundError:
            pass

    def set(self, key, value):
        # Persist the write before changing the in-memory index.
        with open(self.filename, "a", encoding="utf-8") as log:
            log.write(json.dumps([key, value]) + "\n")
            log.flush()
            os.fsync(log.fileno())
        self.update_index(key, value)

    def get(self, key):
        index = self.find(key)
        if index == -1:
            return "(nil)"
        return self.entries[index][1]


def main():
    database = Database()

    # No prompts: automated testers must read only command results.
    for line in sys.stdin:
        parts = line.strip().split(maxsplit=2)
        if not parts:
            continue

        command = parts[0].upper()

        if command == "EXIT" and len(parts) == 1:
            break
        if command == "SET" and len(parts) == 3:
            database.set(parts[1], parts[2])
            print("OK", flush=True)
        elif command == "GET" and len(parts) == 2:
            print(database.get(parts[1]), flush=True)
        else:
            print("ERROR: Invalid command", flush=True)


if __name__ == "__main__":
    main()