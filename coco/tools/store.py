"""Local SQLite store: notes, reminders, contacts, chat history and activity."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL,
    done_at REAL
);
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    due REAL NOT NULL,
    kind TEXT NOT NULL DEFAULT 'reminder',
    lang TEXT NOT NULL DEFAULT 'en',
    created REAL NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    shown INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS chat_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time REAL NOT NULL,
    source TEXT NOT NULL,          -- typed | voice | phone | coco (she spoke first)
    question TEXT NOT NULL DEFAULT '',
    answer TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time REAL NOT NULL,
    kind TEXT NOT NULL,            -- tool | system
    name TEXT NOT NULL,            -- spotify, screenshot, ... / started, stopped, ...
    detail TEXT NOT NULL DEFAULT '',
    ok INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS chat_log_time ON chat_log (time);
CREATE INDEX IF NOT EXISTS activity_time ON activity (time);
"""


@dataclass
class Note:
    id: int
    text: str
    done: bool
    created: float


@dataclass
class Contact:
    id: int
    name: str
    aliases: list[str]  # nicknames: "Mom", "Mummy", "Maa"
    phone: str  # digits with country code, e.g. 919876543210


@dataclass
class Chat:
    id: int
    time: float
    source: str
    question: str
    answer: str


@dataclass
class Activity:
    id: int
    time: float
    kind: str
    name: str
    detail: str
    ok: bool


@dataclass
class Reminder:
    id: int
    text: str
    due: float  # unix time
    kind: str  # "reminder" | "timer"
    lang: str  # "en" | "hinglish": how she says it
    created: float
    done: bool = False
    shown: bool = False  # she already brought it up (waiting for Done / Snooze)


class Store:
    def __init__(self, path: Path | str, clock=time.time) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=5.0)
        self._db.execute("PRAGMA journal_mode=WAL")  # Coco and the hub read/write together
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _run(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._lock:
            cur = self._db.execute(sql, args)
            rows = cur.fetchall()
            self._db.commit()
            self.last_id = cur.lastrowid
            return rows

    # --- notes
    def add_note(self, text: str) -> Note:
        now = self.clock()
        self._run("INSERT INTO notes (text, created) VALUES (?, ?)", (text, now))
        return Note(self.last_id, text, False, now)

    def notes(self, include_done: bool = False) -> list[Note]:
        sql = "SELECT id, text, done, created FROM notes"
        if not include_done:
            sql += " WHERE done = 0"
        return [Note(r[0], r[1], bool(r[2]), r[3]) for r in self._run(sql + " ORDER BY id")]

    def set_note_done(self, note_id: int, done: bool = True) -> None:
        self._run("UPDATE notes SET done = ?, done_at = ? WHERE id = ?",
                  (int(done), self.clock() if done else None, note_id))  # fmt: skip

    def delete_note(self, note_id: int) -> None:
        self._run("DELETE FROM notes WHERE id = ?", (note_id,))

    def delete_all_notes(self) -> int:
        n = len(self.notes(include_done=True))
        self._run("DELETE FROM notes")
        return n

    # --- contacts
    def add_contact(self, name: str, phone: str, aliases: list[str] | None = None) -> Contact:
        al = ", ".join(a.strip() for a in (aliases or []) if a.strip())
        self._run("INSERT INTO contacts (name, aliases, phone) VALUES (?, ?, ?)",
                  (name.strip(), al, phone))  # fmt: skip
        return Contact(self.last_id, name.strip(), [a for a in al.split(", ") if a], phone)

    def contacts(self) -> list[Contact]:
        rows = self._run("SELECT id, name, aliases, phone FROM contacts ORDER BY name")
        return [Contact(r[0], r[1], [a for a in r[2].split(", ") if a], r[3]) for r in rows]

    def delete_contact(self, contact_id: int) -> None:
        self._run("DELETE FROM contacts WHERE id = ?", (contact_id,))

    # --- reminders
    def add_reminder(self, text: str, due: float, kind: str = "reminder", lang="en") -> Reminder:
        now = self.clock()
        self._run(
            "INSERT INTO reminders (text, due, kind, lang, created) VALUES (?, ?, ?, ?, ?)",
            (text, due, kind, lang, now),
        )
        return Reminder(self.last_id, text, due, kind, lang, now)

    def reminders(self, include_done: bool = False) -> list[Reminder]:
        sql = "SELECT id, text, due, kind, lang, created, done, shown FROM reminders"
        if not include_done:
            sql += " WHERE done = 0"
        return [
            Reminder(r[0], r[1], r[2], r[3], r[4], r[5], bool(r[6]), bool(r[7]))
            for r in self._run(sql + " ORDER BY due")
        ]

    def due_reminders(self, now: float) -> list[Reminder]:
        return [r for r in self.reminders() if r.due <= now and not r.shown]

    def next_due(self) -> float | None:
        pending = [r.due for r in self.reminders() if not r.shown]
        return min(pending) if pending else None

    def mark_shown(self, reminder_id: int) -> None:
        self._run("UPDATE reminders SET shown = 1 WHERE id = ?", (reminder_id,))

    def finish_reminder(self, reminder_id: int) -> None:
        self._run("UPDATE reminders SET done = 1 WHERE id = ?", (reminder_id,))

    def snooze(self, reminder_id: int, until: float) -> None:
        self._run("UPDATE reminders SET due = ?, shown = 0 WHERE id = ?", (until, reminder_id))

    def delete_reminder(self, reminder_id: int) -> None:
        self._run("DELETE FROM reminders WHERE id = ?", (reminder_id,))

    def delete_all_reminders(self) -> int:
        n = len(self.reminders())
        self._run("DELETE FROM reminders WHERE done = 0")
        return n

    # --- chat history
    def add_chat(self, source: str, question: str = "", answer: str = "") -> int:
        self._run("INSERT INTO chat_log (time, source, question, answer) VALUES (?, ?, ?, ?)",
                  (self.clock(), source, question, answer))  # fmt: skip
        return self.last_id

    def set_chat_answer(self, chat_id: int, answer: str) -> None:
        self._run("UPDATE chat_log SET answer = ? WHERE id = ?", (answer, chat_id))

    def chats(self, limit: int = 50, before_id: int | None = None, query: str = "") -> list[Chat]:
        """Newest first. `query` searches questions and answers (case-insensitive)."""
        sql, args = "SELECT id, time, source, question, answer FROM chat_log WHERE 1=1", []
        if before_id is not None:
            sql += " AND id < ?"
            args.append(before_id)
        if query:
            sql += " AND (question LIKE ? OR answer LIKE ?)"
            args += [f"%{query}%", f"%{query}%"]
        rows = self._run(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))
        return [Chat(*r) for r in rows]

    def chat(self, chat_id: int) -> Chat | None:
        rows = self._run("SELECT id, time, source, question, answer FROM chat_log WHERE id = ?",
                         (chat_id,))  # fmt: skip
        return Chat(*rows[0]) if rows else None

    def count_chats(self, since: float = 0.0) -> int:
        return self._run("SELECT COUNT(*) FROM chat_log WHERE time >= ?", (since,))[0][0]

    def oldest_chat_time(self) -> float | None:
        rows = self._run("SELECT MIN(time) FROM chat_log")
        return rows[0][0] if rows and rows[0][0] is not None else None

    def prune_chats(self, keep_days: int) -> int:
        """Delete chat messages older than keep_days (0 = keep forever). Returns how many."""
        if keep_days <= 0:
            return 0
        cutoff = self.clock() - keep_days * 86400
        n = self._run("SELECT COUNT(*) FROM chat_log WHERE time < ?", (cutoff,))[0][0]
        if n:
            self._run("DELETE FROM chat_log WHERE time < ?", (cutoff,))
        return n

    def delete_all_chats(self) -> int:
        n = self.count_chats()
        self._run("DELETE FROM chat_log")
        return n

    # --- activity
    def log_activity(self, kind: str, name: str, detail: str = "", ok: bool = True) -> None:
        self._run("INSERT INTO activity (time, kind, name, detail, ok) VALUES (?, ?, ?, ?, ?)",
                  (self.clock(), kind, name, detail[:500], int(ok)))  # fmt: skip

    def activity(self, limit: int = 100) -> list[Activity]:
        rows = self._run("SELECT id, time, kind, name, detail, ok FROM activity "
                         "ORDER BY id DESC LIMIT ?", (limit,))  # fmt: skip
        return [Activity(r[0], r[1], r[2], r[3], r[4], bool(r[5])) for r in rows]

    def prune(self, keep_days: float = 90.0) -> None:
        """Activity older than `keep_days` goes (chat history stays until you delete it)."""
        self._run("DELETE FROM activity WHERE time < ?", (self.clock() - keep_days * 86400,))

    def reset_shown(self) -> None:
        """At startup: reminders she showed but you never answered come back."""
        self._run("UPDATE reminders SET shown = 0 WHERE done = 0")
