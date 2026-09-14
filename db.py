#!/usr/bin/env python3
"""Database layer for The Reading Room.

Backend selection:
  * If the ``DATABASE_URL`` env var is set -> Postgres (via psycopg2).
  * Otherwise -> local SQLite (exactly as before).

The rest of the app keeps writing SQLite-style SQL with ``?``
placeholders; this module translates them to ``%s`` for psycopg2 and
hands back dict-like rows, so the application code is backend-agnostic.
"""

import os
import sqlite3

from flask import g

DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SQLITE_PATH = os.environ.get(
    "DATABASE_PATH", os.path.join(BASE_DIR, "data", "bookclub.db")
)

_PK = "SERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS members (
    id {_PK},
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS books (
    id {_PK},
    title TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    chapters_total INTEGER NOT NULL CHECK (chapters_total > 0),
    recommended_by INTEGER NOT NULL REFERENCES members(id),
    status TEXT NOT NULL DEFAULT 'reading' CHECK (status IN ('reading', 'completed')),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS progress (
    member_id INTEGER NOT NULL REFERENCES members(id),
    book_id INTEGER NOT NULL REFERENCES books(id),
    chapters_completed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (member_id, book_id)
);
CREATE TABLE IF NOT EXISTS chapter_notes (
    member_id INTEGER NOT NULL REFERENCES members(id),
    book_id INTEGER NOT NULL REFERENCES books(id),
    chapter INTEGER NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (member_id, book_id, chapter)
);
CREATE TABLE IF NOT EXISTS book_notes (
    member_id INTEGER NOT NULL REFERENCES members(id),
    book_id INTEGER NOT NULL REFERENCES books(id),
    text TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (member_id, book_id)
);
CREATE TABLE IF NOT EXISTS essays (
    member_id INTEGER NOT NULL REFERENCES members(id),
    book_id INTEGER NOT NULL REFERENCES books(id),
    text TEXT NOT NULL DEFAULT '',
    submitted_at TEXT,
    PRIMARY KEY (member_id, book_id)
);
"""

_MEMBER_SEED = (
    "INSERT INTO members (name) VALUES (%s) ON CONFLICT (name) DO NOTHING"
    if USE_POSTGRES
    else "INSERT OR IGNORE INTO members (name) VALUES (?)"
)


class _Cursor:
    """Wraps a DB-API cursor: translates ``?`` -> ``%s`` on Postgres."""

    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql, params=()):
        if USE_POSTGRES:
            sql = sql.replace("?", "%s")
        # NB: psycopg2's execute() returns None (unlike sqlite3's, which
        # returns the cursor), so always hand back this wrapper.
        self._cursor.execute(sql, params)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor)

    @property
    def lastrowid(self):
        return self._cursor.lastrowid


class _Connection:
    """Wraps a DB-API connection so app code keeps one call style."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        return _Cursor(self._conn.cursor()).execute(sql, params)

    def executescript(self, sql):
        """Run a multi-statement schema script (schema has no placeholders)."""
        if USE_POSTGRES:
            self.execute(sql)
        else:
            self._conn.executescript(sql)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def connect():
    """Open a new backend connection."""
    if USE_POSTGRES:
        conn = psycopg2.connect(
            DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor
        )
        return _Connection(conn)
    parent = os.path.dirname(SQLITE_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    raw = sqlite3.connect(SQLITE_PATH)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    return _Connection(raw)


def get_db():
    """Per-request connection, closed automatically at teardown."""
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = connect()
    try:
        db.executescript(SCHEMA)
        for name in ("Robel", "Bitania"):
            db.execute(_MEMBER_SEED, (name,))
        db.commit()
    finally:
        db.close()


def insert_returning_id(db, sql, params):
    """Run an INSERT and return the new row's id on either backend.

    Postgres has no ``lastrowid``; use ``RETURNING id`` instead.
    """
    if USE_POSTGRES:
        row = db.execute(sql.rstrip().rstrip(";") + " RETURNING id", params).fetchone()
        return row["id"]
    cur = db.execute(sql, params)
    return cur.lastrowid
