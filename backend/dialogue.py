"""Dialogue state machine (Team B).

States: collecting -> await_confirm -> saved.  Nothing is saved without an explicit yes in await_confirm.
The beneficiary is never guessed: duplicate names force an exact-ID answer.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .danger import SYMPTOMS, check_danger
from .db import BeneficiaryRegister, VisitDB, new_draft_id
from .llm import LLMError, rule_extract, validate_extraction
from .phrases import VISIT_LABELS, referral, t

YES = {"yes", "yeah", "yep", "ok", "okay", "correct", "save", "haan", "han", "haa", "ha", "ji", "theek",
       "thik", "sahi", "हाँ", "हां", "हा", "जी", "ठीक", "सही", "सेव"}
NO = {"no", "nope", "nahi", "nahin", "galat", "cancel", "नहीं", "नही", "गलत"}


def _words(text: str) -> list[str]:
    return re.sub(r"[.,!?;:।\"'()]", " ", text.lower()).split()


@dataclass
class DialogueSession:
    session_id: str
    lang: str = "en"
    state: str = "collecting"
    form: dict = field(default_factory=lambda: {"beneficiary_id": None, "visit_type": None,
                                                "concerns": None, "follow_up_days": None})
    need_id: bool = False
    candidates: list = field(default_factory=list)
    draft_id: str = field(default_factory=new_draft_id)
    transcript: list = field(default_factory=list)
    danger: dict = field(default_factory=dict)       # sticky for the visit
    saved_record_id: int | None = None
    ctx: dict = field(default_factory=dict)          # provider scratch (e.g. mock STT cursor)
    last_seen: float = field(default_factory=time.time)

    def new_visit(self) -> None:
        self.state, self.need_id, self.candidates = "collecting", False, []
        self.form = {"beneficiary_id": None, "visit_type": None, "concerns": None, "follow_up_days": None}
        self.draft_id, self.transcript, self.danger, self.saved_record_id = new_draft_id(), [], {}, None


class SessionStore:
    def __init__(self, ttl: float = 3600):
        self._s: dict[str, DialogueSession] = {}
        self.ttl = ttl

    def create(self, lang: str) -> DialogueSession:
        now = time.time()
        for k in [k for k, v in self._s.items() if now - v.last_seen > self.ttl]:
            del self._s[k]
        import uuid
        s = DialogueSession(session_id=uuid.uuid4().hex, lang=lang)
        self._s[s.session_id] = s
        return s

    def get(self, sid: str | None) -> DialogueSession | None:
        s = self._s.get(sid or "")
        if s:
            s.last_seen = time.time()
        return s


class DialogueEngine:
    def __init__(self, register: BeneficiaryRegister, db: VisitDB, llm, require_verified_danger: bool = False):
        self.reg, self.db, self.llm, self.rv = register, db, llm, require_verified_danger

    # ---------- public ----------
    async def process(self, s: DialogueSession, text: str) -> dict:
        text = (text or "").strip()
        s.transcript.append(text)
        s.transcript = s.transcript[-60:]
        warnings: list[str] = []
        words = _words(text)
        yes = bool(words) and words[0] in YES and not any(w in NO for w in words)
        no = any(w in NO for w in words)

        if s.state == "saved":
            if yes or no:
                return self.snapshot(s, t(s.lang, "already_saved"), warnings)
            s.new_visit()
            s.transcript.append(text)

        ex = await self._extract(text, warnings)
        ident = self._identify(text, ex)
        fields = bool(ex["visit_type"] or ex["concerns"] or ex["no_concerns"] or ex["follow_up_days"]
                      or ident[0] != "none")

        if s.state == "await_confirm" and not fields:
            if yes:
                return self._save(s, warnings)
            if no:
                s.state = "collecting"
                return self.snapshot(s, t(s.lang, "not_saved"), warnings)
            return self.snapshot(s, t(s.lang, "confirm_again"), warnings)

        lead: list[str] = []
        lead_is_question = self._apply(s, ex, ident, lead)

        # danger check: independent rules + allowlisted LLM candidates; sticky per visit
        res = check_danger(text, concerns=s.form["concerns"] or [], llm_candidates=ex["danger_candidates"],
                           lang=s.lang, require_verified=self.rv)
        new = [x for x in res["signs"] if x["id"] not in s.danger]
        for x in new:
            s.danger[x["id"]] = x
        if new:
            lead.insert(0, referral(s.lang))

        if not fields:
            lead.append(t(s.lang, "didnt_understand"))

        missing = self._missing(s)
        if missing:
            s.state = "collecting"
            if not lead_is_question:
                lead.append(self._question(s, missing[0]))
        else:
            s.state = "await_confirm"
            lead.append(self._readback(s))
        return self.snapshot(s, " ".join(lead), warnings)

    def snapshot(self, s: DialogueSession, reply: str = "", warnings=None) -> dict:
        rec = self.reg.get(s.form["beneficiary_id"])
        f = s.form
        return {
            "state": s.state, "reply": reply, "lang": s.lang, "warnings": list(warnings or []),
            "form": {"beneficiary_id": f["beneficiary_id"], "beneficiary_name": rec["name"] if rec else None,
                     "visit_type": f["visit_type"],
                     "visit_label": VISIT_LABELS[s.lang][f["visit_type"]] if f["visit_type"] else None,
                     "concerns": f["concerns"],
                     "concern_labels": [SYMPTOMS[k][s.lang] for k in (f["concerns"] or [])],
                     "follow_up_days": f["follow_up_days"]},
            "missing": self._missing(s),
            "candidates": s.candidates if s.need_id else [],
            "danger": {"signs": list(s.danger.values()), "referral": referral(s.lang) if s.danger else None},
            "saved_record_id": s.saved_record_id,
            "followups": self._followups() if s.state == "saved" else [],
        }

    # ---------- internals ----------
    async def _extract(self, text: str, warnings: list) -> dict:
        try:
            return validate_extraction(await self.llm.extract(text))
        except (LLMError, ValueError, Exception):  # any LLM failure -> deterministic fallback
            warnings.append("llm_unavailable_used_rules")
            return validate_extraction(rule_extract(text))

    def _identify(self, text: str, ex: dict):
        ids = self.reg.find_ids(text)
        if not ids and ex["beneficiary_id"]:
            n = self.reg.normalize_id(ex["beneficiary_id"])
            ids = [n] if n else ["INVALID"]
        if ids:
            rec = self.reg.get(ids[-1])
            return ("id_ok", rec) if rec else ("id_bad", None)
        hits = self.reg.find_by_name(text, ex["beneficiary_name"])
        if len(hits) == 1:
            return ("one", hits[0])
        if len(hits) > 1:
            return ("many", hits)
        return ("zero", None) if ex["beneficiary_name"] else ("none", None)

    def _apply(self, s: DialogueSession, ex: dict, ident, lead: list) -> bool:
        f, kind = s.form, ident[0]
        question = False
        if ex["visit_type"]:
            f["visit_type"] = ex["visit_type"]
        if ex["concerns"]:
            f["concerns"] = sorted(set(f["concerns"] or []) | set(ex["concerns"]))
        elif ex["no_concerns"]:
            f["concerns"] = []
        if ex["follow_up_days"]:
            f["follow_up_days"] = ex["follow_up_days"]
        if kind in ("id_ok", "one"):
            f["beneficiary_id"], s.need_id, s.candidates = ident[1]["id"], False, []
        elif kind == "many":
            f["beneficiary_id"], s.need_id = None, True
            s.candidates = [self.reg.public_candidate(r) for r in ident[1]]
            lead.append(t(s.lang, "ask_id_multi", ids=", ".join(c["id"] for c in s.candidates)))
            question = True
        elif kind == "id_bad":
            f["beneficiary_id"], s.need_id = None, True
            lead.append(t(s.lang, "id_invalid"))
            question = True
        elif kind == "zero":
            f["beneficiary_id"], s.need_id = None, True
            lead.append(t(s.lang, "name_not_found"))
            question = True
        return question

    @staticmethod
    def _missing(s: DialogueSession) -> list[str]:
        m = []
        if not s.form["beneficiary_id"]:
            m.append("beneficiary")
        if not s.form["visit_type"]:
            m.append("visit_type")
        if s.form["concerns"] is None:
            m.append("concerns")
        return m

    def _question(self, s: DialogueSession, what: str) -> str:
        if what == "beneficiary":
            if s.need_id and s.candidates:
                return t(s.lang, "ask_id_multi", ids=", ".join(c["id"] for c in s.candidates))
            return t(s.lang, "ask_beneficiary")
        return t(s.lang, "ask_visit_type" if what == "visit_type" else "ask_concerns")

    def _readback(self, s: DialogueSession) -> str:
        f, rec = s.form, self.reg.get(s.form["beneficiary_id"])
        concerns = ", ".join(SYMPTOMS[k][s.lang] for k in f["concerns"]) or t(s.lang, "none")
        fu = t(s.lang, "followup_days", n=f["follow_up_days"]) if f["follow_up_days"] else t(s.lang, "none")
        return t(s.lang, "readback", name=rec["name"], bid=f["beneficiary_id"],
                 visit=VISIT_LABELS[s.lang][f["visit_type"]], concerns=concerns, followup=fu)

    def _save(self, s: DialogueSession, warnings: list) -> dict:
        f = s.form
        payload = {"beneficiary_id": f["beneficiary_id"], "visit_type": f["visit_type"],
                   "concerns": f["concerns"], "follow_up_days": f["follow_up_days"],
                   "danger_signs": sorted(s.danger), "language": s.lang}
        try:
            rid, _created = self.db.save_visit(s.draft_id, s.session_id, f["beneficiary_id"], payload,
                                               s.transcript, bool(s.danger))
        except Exception:
            warnings.append("db_error")
            return self.snapshot(s, t(s.lang, "save_failed"), warnings)
        s.state, s.saved_record_id = "saved", rid
        reply = t(s.lang, "saved") + (" " + referral(s.lang) if s.danger else "")
        return self.snapshot(s, reply, warnings)

    def _followups(self) -> list[dict]:
        out = []
        for fu in self.db.list_followups():
            rec = self.reg.get(fu["beneficiary_id"])
            out.append({**fu, "name": rec["name"] if rec else None})
        return out
