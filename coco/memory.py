"""Long-term memory: short facts about the owner, recalled by meaning."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)

DIM = 512

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    created REAL NOT NULL,
    used REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'chat',
    vec BLOB
);
"""

# not worth remembering: the model sometimes "remembers" the conversation itself
_TRIVIA = re.compile(
    r"^(none|nothing|n/a|no|-|\W*)$|"
    r"\b(asked|is asking|wants to know|said hi|greeted|is talking to|chatting with|thanked)\b|"
    r"\b(right now|currently|just now|today)\b.*\b(asked|said|is)\b",
    re.IGNORECASE,
)
_WORDS = re.compile(r"[a-z0-9ऀ-ॿ]+")
STOP = {"the", "a", "an", "is", "are", "was", "my", "your", "to", "of", "and", "in", "on",
        "for", "it", "i", "me", "you", "what", "do", "does", "did", "about", "that", "this",
        "hai", "ka", "ki", "ke", "ko", "mera", "meri", "kya"}  # fmt: skip


@dataclass
class Memory:
    id: int
    text: str
    created: float
    used: float = 0.0
    source: str = "chat"
    score: float = 0.0


def ollama_embedder(host: str, model: str = "qwen3-embedding:0.6b") -> Callable:
    """embed(texts) -> list of vectors, on the CPU (the GPU is full of ears + brain)."""
    import ollama

    client = ollama.Client(host=host, timeout=20)

    def embed(texts: list[str]) -> list[list[float]]:
        r = client.embed(model=model, input=texts, dimensions=DIM, keep_alive="10m",
                         options={"num_gpu": 0})  # fmt: skip
        return [list(v) for v in r.embeddings]

    return embed


def _words(text: str) -> set[str]:
    return {w for w in _WORDS.findall(text.lower()) if w not in STOP and len(w) > 1}


class MemoryBook:
    def __init__(
        self,
        store,
        embed: Callable[[list[str]], list[list[float]]] | None = None,
        *,
        per_day: int = 15,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store  # coco.tools.store.Store (shares its connection + lock)
        self.embed = embed
        self.per_day = per_day
        self.clock = clock
        self._lock = threading.Lock()
        with store._lock:
            store._db.executescript(_SCHEMA)
            store._db.commit()

    # --- helpers
    def _vec(self, text: str) -> np.ndarray | None:
        if self.embed is None:
            return None
        try:
            v = np.asarray(self.embed([text])[0], dtype=np.float32)
        except Exception as exc:  # noqa: BLE001 - model missing / Ollama down: words only
            log.info("memory: no embedding (%s); matching words instead", exc)
            return None
        n = float(np.linalg.norm(v))
        return v / n if n else None

    def _rows(self) -> list[tuple]:
        return self.store._run("SELECT id, text, created, used, source, vec FROM memories")

    def all(self) -> list[Memory]:
        return [Memory(r[0], r[1], r[2], r[3], r[4]) for r in reversed(self._rows())]

    def count(self) -> int:
        return self.store._run("SELECT COUNT(*) FROM memories")[0][0]

    def _scored(self, query: str) -> list[Memory]:
        rows = self._rows()
        if not rows:
            return []
        q = self._vec(query)
        qw = _words(query)
        out = []
        for r in rows:
            score = 0.0
            if q is not None and r[5]:
                score = float(np.dot(q, np.frombuffer(r[5], dtype=np.float32)))
            else:  # words in common (no embedding model)
                mw = _words(r[1])
                score = len(qw & mw) / max(1, min(len(qw), len(mw))) if qw and mw else 0
            out.append(Memory(r[0], r[1], r[2], r[3], r[4], score))
        return sorted(out, key=lambda m: -m.score)

    # --- API
    def warm_up(self) -> None:
        """Load the embedding model (first use takes ~6 s on the CPU)."""
        self._vec("hello")

    def worth_keeping(self, text: str) -> bool:
        text = (text or "").strip()
        return 8 <= len(text) <= 300 and not _TRIVIA.search(text)

    def add(self, text: str, source: str = "chat", force: bool = False) -> Memory | None:
        """Save a fact (None: not worth it, or today's limit). A near-duplicate is updated
        instead of saved twice ("exam on Friday" -> "exam moved to Monday")."""
        text = re.sub(r"\s+", " ", (text or "").strip().strip('"'))
        if not force and not self.worth_keeping(text):
            return None
        with self._lock:
            now = self.clock()
            if not force:
                today = self.store._run(
                    "SELECT COUNT(*) FROM memories WHERE created >= ? AND source = 'chat'",
                    (now - 86400,),
                )[0][0]
                if today >= self.per_day:
                    log.info("memory: today's limit reached, not saving %r", text)
                    return None
            vec = self._vec(text)
            best = self._scored(text)[:1]
            if best and best[0].score >= (0.88 if vec is not None else 0.75):
                old = best[0]
                blob = vec.tobytes() if vec is not None else None
                self.store._run(
                    "UPDATE memories SET text = ?, created = ?, vec = ? WHERE id = ?",
                    (text, now, blob, old.id),
                )
                log.info("memory: updated #%d %r -> %r", old.id, old.text, text)
                return Memory(old.id, text, now, source=source)
            self.store._run(
                "INSERT INTO memories (text, created, source, vec) VALUES (?, ?, ?, ?)",
                (text, now, source, vec.tobytes() if vec is not None else None),
            )
            log.info("memory: saved %r", text)
            return Memory(self.store.last_id, text, now, source=source)

    def recall(self, query: str, k: int = 4, min_score: float = 0.55) -> list[Memory]:
        """Memories that fit a question, best first (marks them as used)."""
        hits = [m for m in self._scored(query)[:k] if m.score >= min_score]
        if hits:
            now = self.clock()
            for m in hits:
                self.store._run("UPDATE memories SET used = ? WHERE id = ?", (now, m.id))
        return hits

    def find(self, query: str, k: int = 3) -> list[Memory]:
        """Candidates for "forget that ...": closest first."""
        return [m for m in self._scored(query)[:k] if m.score >= 0.35]

    def delete(self, memory_id: int) -> None:
        self.store._run("DELETE FROM memories WHERE id = ?", (memory_id,))

    def delete_all(self) -> int:
        n = self.count()
        self.store._run("DELETE FROM memories")
        return n
