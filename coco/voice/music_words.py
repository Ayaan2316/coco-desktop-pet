"""Corrects misheard song and artist names by how they sound."""

from __future__ import annotations

import difflib
import json
import logging
import re
import threading
from pathlib import Path

from coco.core.paths import user_data_dir

log = logging.getLogger(__name__)

BUILTIN_ARTISTS = [
    "Arijit Singh", "Pritam", "Seedhe Maut", "Divine", "KR$NA", "Badshah", "AP Dhillon",
    "Diljit Dosanjh", "Shreya Ghoshal", "Atif Aslam", "Jubin Nautiyal", "Darshan Raval",
    "Anuv Jain", "Prateek Kuhad", "Raftaar", "Emiway Bantai", "MC Stan", "King", "Karan Aujla",
    "Sidhu Moose Wala", "Talwiinder", "Aditya Rikhari", "Vishal Mishra", "B Praak",
    "Neha Kakkar", "Sonu Nigam", "Lata Mangeshkar", "Kishore Kumar", "A R Rahman",
    "Shankar Mahadevan", "Amit Trivedi", "Mohit Chauhan", "Sunidhi Chauhan", "Armaan Malik",
    "Yo Yo Honey Singh", "Guru Randhawa", "Hanumankind", "Bharg", "Kaifi Khalil",
    "Faheem Abdullah", "Shubh", "Sachet Tandon", "Vishal Dadlani", "Shaan", "KK",
    "Mohammed Rafi", "Asha Bhosle", "Udit Narayan", "Alka Yagnik", "Kumar Sanu",
    "Sachin-Jigar", "Vishal-Shekhar", "Salim-Sulaiman", "Anirudh Ravichander", "Sid Sriram",
    "The Local Train", "When Chai Met Toast", "Lucky Ali", "Nucleya", "Ritviz", "Mitraz",
    "Ed Sheeran", "Taylor Swift", "The Weeknd", "Billie Eilish", "Dua Lipa", "Coldplay",
    "Imagine Dragons", "Post Malone", "Drake", "Eminem", "Justin Bieber", "Bruno Mars",
]  # fmt: skip

# what "play" turns into when the start of it is clipped or misheard
_PLAY = (r"play|played|plays|put on|lay|le|lay|let|lea|bless|place|claim|clay|pay|pray|"
         r"blaze|yah|ya|mera|the|a")  # fmt: skip
_REQUEST = re.compile(
    rf"^\W*(?P<verb>{_PLAY})\b[\s,.]*(?P<q>.+?)[\s.!?]*$|"
    r"^\W*(?P<q2>.+?)\s+(?:ke\s+gaane\s+)?(?:lagao|bajao|bachao|chalao|laga\s+do|baja\s+do|"
    r"chala\s+do|play\s+karo)[\s.!?]*$",
    re.IGNORECASE,
)
_TAIL = re.compile(r"\s+(?P<tail>on\s+(?:youtube|you\s*tube|yt|your\s+book|spotify))$", re.I)


def sound_key(text: str) -> str:
    """A rough "how it sounds" spelling: Seedhe Maut ~ sidhe mouth, Kesariya ~ kesaria."""
    s = re.sub(r"[^a-z ]+", "", text.lower().replace("$", "s"))
    for a, b in (("ph", "f"), ("sh", "s"), ("ch", "c"), ("kh", "k"), ("gh", "g"), ("th", "t"),
                 ("dh", "d"), ("bh", "b"), ("jh", "j"), ("ck", "k"), ("q", "k"), ("x", "ks"),
                 ("z", "j"), ("w", "v"), ("c", "k"), ("y", "i"), ("ee", "i"), ("oo", "u"),
                 ("ou", "u"), ("au", "o"), ("aa", "a"), ("e", "i")):  # fmt: skip
        s = s.replace(a, b)
    s = re.sub(r"(.)\1+", r"\1", s)  # double letters
    return s.replace(" ", "")


def similarity(a: str, b: str) -> float:
    ka, kb = sound_key(a), sound_key(b)
    if not ka or not kb:
        return 0.0
    return difflib.SequenceMatcher(None, ka, kb).ratio()


class MusicWords:
    def __init__(self, path: Path | None = None, builtin: list[str] | None = None) -> None:
        self.path = path or user_data_dir() / "music_words.json"
        self.builtin = list(BUILTIN_ARTISTS if builtin is None else builtin)
        self._lock = threading.Lock()
        self.learned: list[str] = []
        self.mine: list[str] = []  # added by you on the dashboard
        self._mtime = 0.0
        self._load()

    def _load(self) -> None:
        try:
            self._mtime = self.path.stat().st_mtime
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.learned = [str(x) for x in data.get("learned", [])]
            self.mine = [str(x) for x in data.get("mine", [])]
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {"learned": self.learned[-500:], "mine": self.mine}
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            self._mtime = self.path.stat().st_mtime
        except OSError:
            log.warning("Could not save %s", self.path)

    @property
    def names(self) -> list[str]:
        try:
            if self.path.stat().st_mtime != self._mtime:
                self._load()  # (the dashboard saved a new list)
        except OSError:
            pass
        seen, out = set(), []
        for n in [*self.mine, *self.learned, *self.builtin]:
            k = n.lower()
            if k not in seen and len(sound_key(n)) >= 3:
                seen.add(k)
                out.append(n)
        return out

    def learn_played(self, now_playing: str) -> None:
        """'Kesariya (From "Brahmastra") by Pritam, Arijit Singh' -> the title + each artist."""
        title, _, artists = now_playing.partition(" by ")
        title = re.sub(r"\s*[\(\[].*?[\)\]]", "", title).strip()  # (From "...") / [Remix]
        names = [title, *[a.strip() for a in artists.split(",")]]
        with self._lock:
            added = False
            for n in names:
                n = n.strip(" -–|")
                if 2 <= len(n) <= 60 and n.lower() not in (x.lower() for x in self.learned):
                    self.learned.append(n)
                    added = True
            if added:
                self._save()

    def set_mine(self, names: list[str]) -> None:
        with self._lock:
            self.mine = [n.strip() for n in names if n.strip()][:500]
            self._save()

    # --- fixing
    def fix_query(self, query: str, threshold: float = 0.72) -> tuple[str, float]:
        """Replace misheard names in a song request with the names she knows.
        Returns (fixed query, how sure: the worst accepted match, or 0 if nothing matched)."""
        words = query.split()
        names = self.names
        fixed: list[tuple[int, int, str, float]] = []
        used = [False] * len(words)
        for _ in range(3):  # up to 3 names in one request ("X by A and B")
            best = None
            for i in range(len(words)):
                for j in range(i + 1, min(len(words), i + 4) + 1):
                    if any(used[i:j]):
                        continue
                    span = " ".join(words[i:j])
                    ks = sound_key(span)
                    if len(ks) < 3:
                        continue
                    for n in names:
                        kn = sound_key(n)
                        if abs(len(kn) - len(ks)) > max(3, len(kn) // 2):
                            continue
                        r = difflib.SequenceMatcher(None, ks, kn).ratio()
                        # short names (King, KK, Shaan) match common words: near-exact only
                        need = max(threshold, 0.9) if len(kn) <= 5 else threshold
                        if r >= need and (best is None or r > best[3]):
                            best = (i, j, n, r)
            if best is None:
                break
            i, j, n, r = best
            fixed.append(best)
            for k in range(i, j):
                used[k] = True
        if not fixed:
            return query, 0.0
        out, k = [], 0
        for i, j, n, _r in sorted(fixed):
            out += words[k:i] + [n]
            k = j
        out += words[k:]
        return " ".join(out), min(r for *_x, r in fixed)

    def fix_request(self, text: str, need_verb: bool = True) -> tuple[str, float] | None:
        """'Le madira by sidhe maut.' -> ('play Madira by Seedhe Maut', 0.9); None when it
        doesn't look like a song request or no known name is in it. need_verb=False: the
        other ear already heard "play", so the whole text may be the song's name."""
        m = _REQUEST.match(text.strip())
        if m:
            query = (m.group("q") or m.group("q2") or "").strip()
        elif not need_verb:
            query = _LEAD.sub("", text).strip(" .,!?")  # ("Lekin shariya": a clipped "play")
        else:
            return None
        tail = ""
        t = _TAIL.search(query)
        if t:
            query, tail = (
                query[: t.start()],
                " on YouTube" if "spotify" not in t.group("tail").lower() else " on Spotify",
            )
        query = re.sub(r"\s+(?:by|bye|buy|bhai|vaise|baya)\s+", " by ", query, flags=re.I)
        fixed, sure = self.fix_query(query)
        if sure <= 0:
            return None
        return f"play {fixed}{tail}", sure


# --- choosing
PLAYISH = re.compile(
    r"^\W*(?:play|put on)\b|(?:lagao|bajao|chalao|laga do|baja do|chala do)[\s.!?]*$", re.I
)
_LIKED = re.compile(
    r"\b(?:my\s+)?(?:light|like|life|lied|lite)\s+(?:songs|zones|sons|song)\b", re.I
)
_LEAD = re.compile(
    r"^\W*(?:play|played|put on|lay|le|let|lea|mera|yah|ya|bless|place|claim|so|lekin|lake)"
    r"\b[\s,.]*",
    re.I,
)
_WITH_ME = re.compile(r"\bwith me\b", re.I)


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z]+", text.lower()))


def music_pick(english: str, hinglish: str, words: MusicWords | None) -> str | None:
    """Pick the best transcript for a song request heard by both ears, or None.

    Only used when one ear clearly heard "play ..." or "... lagao / bajao";
    other sentences go through the normal rules.
    """
    from coco.behaviour.commands import parse_command

    if words is None or not (PLAYISH.search(english) or PLAYISH.search(hinglish)):
        return None
    for t in (english, hinglish):
        if _LIKED.search(t):
            return "play my liked songs"
    best = None
    for t, other in ((english, hinglish), (hinglish, english)):
        if _WITH_ME.search(t):
            continue  # "play with me" is her play command, not a song
        fixed = words.fix_request(t, need_verb=not PLAYISH.search(other))
        if fixed and (best is None or fixed[1] > best[1]):
            best = fixed
    if best:
        return best[0]
    # The English ear often just copies its hint "Play with me." when it can't make out a
    # Hindi name; the Hinglish ear usually got the name (Spotify's search forgives spelling)
    if (
        _norm(english) == "play with me"
        and hinglish
        and parse_command(hinglish) is None
        and not _WITH_ME.search(hinglish)
    ):
        q = _LEAD.sub("", hinglish).strip(" .,!?")
        if len(q) >= 3:
            return f"play {q}"
    return None
