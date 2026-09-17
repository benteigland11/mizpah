"""Transactional, optimistic JSON aggregate storage with immutable revisions."""
from __future__ import annotations

from contextlib import closing
import json
import sqlite3
from pathlib import Path
from typing import Callable


class Conflict(ValueError):
    """A writer's expected revision is no longer current."""

    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(f"Stale revision {expected}; current revision is {actual}. Reload before editing.")
        self.expected = expected
        self.actual = actual


class RevisionStore:
    """Read without creating files; explicitly commit one complete JSON value."""

    def __init__(self, path: str | Path, timeout: float = 10) -> None:
        self.path = Path(path)
        self.timeout = timeout

    def read(self, revision: int | None = None) -> dict:
        if not self.path.exists():
            if revision is not None:
                raise ValueError("Revision does not exist")
            return {"revision": 0, "data": {}}
        with closing(sqlite3.connect(self.path, timeout=self.timeout)) as db:
            query = "SELECT revision, payload FROM revisions"
            row = db.execute(query + (" ORDER BY revision DESC LIMIT 1" if revision is None else " WHERE revision = ?"), () if revision is None else (revision,)).fetchone()
        if row is None:
            if revision is None:
                return {"revision": 0, "data": {}}
            raise ValueError("Revision does not exist")
        return {"revision": row[0], "data": json.loads(row[1])}

    def commit(self, expected: int, transform: Callable[[dict], dict]) -> dict:
        if type(expected) is not int or expected < 0:
            raise ValueError("Expected revision must be a nonnegative integer")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=self.timeout)
        try:
            db.execute("CREATE TABLE IF NOT EXISTS revisions (revision INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision, payload FROM revisions ORDER BY revision DESC LIMIT 1").fetchone()
            actual, value = (row[0], json.loads(row[1])) if row else (0, {})
            if expected != actual:
                raise Conflict(expected, actual)
            result = transform(value)
            if not isinstance(result, dict):
                raise ValueError("Transform must return a JSON object")
            payload = json.dumps(result, allow_nan=False)
            db.execute("INSERT INTO revisions VALUES (?, ?)", (actual + 1, payload))
            db.commit()
            return {"revision": actual + 1, "data": result}
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
