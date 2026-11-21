"""Encrypted SQLite persistence + synthetic beneficiary register (Team D).

Encrypted (Fernet): form payload and transcript.
Plaintext metadata (needed for lookup/dedupe; documented in README): row id, draft_id,
session_id, beneficiary_id, created_at, danger_flag.
NOTE: the visit row id is NOT the beneficiary ID.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet

SCHEMA = """
CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    beneficiary_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    danger_flag INTEGER NOT NULL DEFAULT 0,
    payload_enc BLOB NOT NULL,
    transcript_enc BLOB NOT NULL
);
"""


class VisitDB:
    def __init__(self, path: str, key: str):
        self.path = str(path)
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path)
        c.execute("PRAGMA secure_delete=ON")
        return c

    def _enc(self, obj) -> bytes:
        return self._fernet.encrypt(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _dec(self, blob: bytes):
        return json.loads(self._fernet.decrypt(blob).decode("utf-8"))

    def save_visit(self, draft_id: str, session_id: str, beneficiary_id: str, form: dict,
                   transcript: list, danger_flag: bool) -> tuple[int, bool]:
        """Idempotent on draft_id. Returns (row_id, created). created=False means duplicate ignored."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO visits (draft_id, session_id, beneficiary_id, created_at, danger_flag, payload_enc, transcript_enc) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(draft_id) DO NOTHING",
                (draft_id, session_id, beneficiary_id, now, int(danger_flag), self._enc(form), self._enc(transcript)))
            created = cur.rowcount == 1
            row = c.execute("SELECT id FROM visits WHERE draft_id=?", (draft_id,)).fetchone()
        return row[0], created

    def get_visit(self, row_id: int) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT id, draft_id, session_id, beneficiary_id, created_at, danger_flag, payload_enc, transcript_enc "
                          "FROM visits WHERE id=?", (row_id,)).fetchone()
        if not r:
            return None
        return {"id": r[0], "draft_id": r[1], "session_id": r[2], "beneficiary_id": r[3], "created_at": r[4],
                "danger_flag": bool(r[5]), "form": self._dec(r[6]), "transcript": self._dec(r[7])}

    def count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM visits").fetchone()[0]

    def list_followups(self) -> list[dict]:
        out = []
        with self._conn() as c:
            rows = c.execute("SELECT id, beneficiary_id, created_at, payload_enc FROM visits ORDER BY id").fetchall()
        for rid, bid, created, blob in rows:
            days = self._dec(blob).get("follow_up_days")
            if days:
                due = datetime.fromisoformat(created) + timedelta(days=int(days))
                out.append({"visit_id": rid, "beneficiary_id": bid, "due_date": due.date().isoformat()})
        return sorted(out, key=lambda x: x["due_date"])

    def delete_all(self, remove_file: bool = False) -> int:
        n = self.count()
        with self._conn() as c:
            c.execute("DELETE FROM visits")
            c.execute("DELETE FROM sqlite_sequence WHERE name='visits'")
        c = sqlite3.connect(self.path)
        c.execute("PRAGMA secure_delete=ON")
        c.execute("VACUUM")
        c.close()
        if remove_file:
            for suffix in ("", "-wal", "-shm", "-journal"):
                try:
                    os.remove(self.path + suffix)
                except FileNotFoundError:
                    pass
        return n


class BeneficiaryRegister:
    """Approved beneficiary register. Demo file is SYNTHETIC; no real database is connected."""

    def __init__(self, path: str):
        with open(path, encoding="utf-8") as f:
            self.records = {r["id"].upper(): r for r in json.load(f)["beneficiaries"]}
        self.prefixes = sorted({re.match(r"[A-Za-z]+", i).group(0) for i in self.records})
        first = next(iter(self.records))
        self.width = len(re.search(r"\d+", first).group(0))

    def get(self, bid: str | None) -> dict | None:
        return self.records.get((bid or "").upper())

    def normalize_spoken(self, text: str) -> str:
        words = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
                 "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
        pat = r"(?i)\b(" + "|".join(self.prefixes) + r")\s+((?:(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|\d)\s*){1,6})"

        def fix(m):
            toks = re.findall(r"zero|oh|one|two|three|four|five|six|seven|eight|nine|\d", m.group(2).lower())
            return m.group(1) + "".join(words.get(t, t) for t in toks) + " "
        return re.sub(pat, fix, text)

    def find_ids(self, text: str) -> list[str]:
        text = self.normalize_spoken(text)
        pat = r"(?i)\b(" + "|".join(self.prefixes) + r")\s*-?\s*(\d{1,6})\b"
        out = []
        for m in re.finditer(pat, text):
            digits = m.group(2)
            digits = digits.zfill(self.width) if len(digits) < self.width else digits
            out.append(m.group(1).upper() + digits)
        return out

    def normalize_id(self, bid: str) -> str | None:
        ids = self.find_ids(bid or "")
        return ids[-1] if ids else None

    @staticmethod
    def _negated(low: str, s: int, e: int) -> bool:
        """'not Sunita' / 'Bharath nahi' -> the named person is being ruled out (a correction)."""
        before = low[max(0, s - 12):s].split()[-1:]
        after = low[e:e + 12].split()[:1]
        return bool(before and before[0].strip(",.") in ("not", "बिना")) or \
            bool(after and after[0].strip(",.") in ("nahi", "nahin", "नहीं", "नही"))

    @staticmethod
    def _last_pos(low: str, alias: str) -> int:
        best = -1
        start = 0
        while True:
            i = low.find(alias, start)
            if i < 0:
                return best
            ok = not (alias[0].isascii() and ((i > 0 and low[i - 1].isalpha()) or
                                              (i + len(alias) < len(low) and low[i + len(alias)].isalpha())))
            if ok and not BeneficiaryRegister._negated(low, i, i + len(alias)):
                best = i
            start = i + 1

    def _hits(self, text: str) -> list[dict]:
        low = text.lower()
        scored = []
        for rec in self.records.values():
            pos = max([self._last_pos(low, a.lower()) for a in rec.get("aliases", []) + [rec["name"]]])
            if pos >= 0:
                scored.append((pos, rec))
        if not scored:
            return []
        top_name = max(scored, key=lambda x: x[0])[1]["name"].lower()  # last-mentioned name wins (corrections)
        return [r for _, r in scored if r["name"].lower() == top_name]

    def find_by_name(self, text: str, extra: str | None = None) -> list[dict]:
        return self._hits(text) or (self._hits(extra) if extra else [])

    @staticmethod
    def public_candidate(rec: dict) -> dict:
        """Minimum needed to tell records apart: ID + village. No health data."""
        return {"id": rec["id"], "village": rec["village"]}


def new_draft_id() -> str:
    return uuid.uuid4().hex
