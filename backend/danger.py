"""Rule-based danger-sign checking (Team D). Works with no LLM.

VERIFICATION STATUS: BLOCKER. The environment that built this had no internet access, so no
guideline text could be checked. Every rule below is therefore marked verified=False and
carries source="PENDING VERIFICATION". Rules are QUALITATIVE only (symptom reported or not);
NO numeric thresholds (BP, temperature, counts) are used anywhere, because none could be verified.
A medical reviewer must replace `source`/`criteria` with the actual guideline citation and flip
`verified` to True. Set DANGER_REQUIRE_VERIFIED=1 to disable all unverified rules.
The checker never diagnoses and never suggests treatment or dosage.
"""
from __future__ import annotations

import re

from .phrases import referral

# key -> labels + trigger phrases (English, romanised Hindi, Devanagari). Also the extraction vocabulary.
SYMPTOMS: dict[str, dict] = {
    "convulsions": {"en": "convulsions / fits", "hi": "दौरे / झटके",
                    "kw": ["convulsion", "seizure", "fits", "jhatke", "दौरे", "दौरा", "झटके", "मिर्गी"]},
    "heavy_bleeding": {"en": "heavy bleeding", "hi": "ज़्यादा रक्तस्राव",
                       "kw": ["heavy bleeding", "excess bleeding", "bleeding", "zyada khoon", "jyada khoon",
                              "ज्यादा खून", "ज़्यादा खून", "खून बह", "रक्तस्राव"]},
    "severe_headache": {"en": "severe headache", "hi": "तेज़ सिरदर्द",
                        "kw": ["severe headache", "bahut sar dard", "bahut sir dard", "tez sar dard",
                               "तेज सिरदर्द", "तेज़ सिरदर्द", "तेज सर दर्द", "बहुत सिरदर्द"]},
    "blurred_vision": {"en": "blurred vision", "hi": "धुंधला दिखना",
                       "kw": ["blurred vision", "blurry vision", "blurred", "dhundhla", "धुंधला"]},
    "reduced_fetal_movement": {"en": "reduced baby movement", "hi": "बच्चे की हलचल कम",
                               "kw": ["baby not moving", "not moving", "reduced movement", "less movement",
                                      "movement kam", "हलचल कम", "हिल नहीं", "hil nahi"]},
    "difficulty_breathing": {"en": "difficulty breathing", "hi": "साँस लेने में तकलीफ़",
                             "kw": ["difficulty breathing", "trouble breathing", "breathless", "saans phool",
                                    "saans lene", "सांस फूल", "साँस फूल", "सांस लेने में", "साँस लेने में"]},
    "unconscious": {"en": "unconscious / fainted", "hi": "बेहोशी",
                    "kw": ["unconscious", "behosh", "बेहोश"]},
    "high_fever": {"en": "high fever", "hi": "तेज़ बुखार",
                   "kw": ["high fever", "very high fever", "tez bukhar", "तेज बुखार", "तेज़ बुखार"]},
    # Non-danger concerns (recorded, never trigger a warning on their own):
    "fever": {"en": "fever", "hi": "बुखार", "kw": ["fever", "bukhar", "बुखार"]},
    "cough": {"en": "cough", "hi": "खाँसी", "kw": ["cough", "khansi", "खांसी", "खाँसी"]},
    "weakness": {"en": "weakness", "hi": "कमज़ोरी", "kw": ["weakness", "kamzori", "कमजोरी", "कमज़ोरी"]},
    "other": {"en": "other problem (not itemised)", "hi": "अन्य समस्या (विवरण नहीं)",
              "kw": ["other problem", "other issue", "something else", "aur koi problem",
                     "और कोई समस्या", "अन्य समस्या", "दूसरी समस्या"]},
}

PENDING_SOURCE = ("PENDING VERIFICATION - candidate danger sign; exact public-health guideline "
                  "(document, edition, page) NOT yet verified")
CRITERIA = "Symptom reported by the ASHA worker (qualitative; no numeric threshold)."

APPROVED_DANGER_KEYS = ("convulsions", "heavy_bleeding", "severe_headache", "blurred_vision",
                        "reduced_fetal_movement", "difficulty_breathing", "unconscious", "high_fever")
DANGER_RULES = {k: {"id": k, "source": PENDING_SOURCE, "criteria": CRITERIA, "verified": False}
                for k in APPROVED_DANGER_KEYS}

_BEFORE_NEG = {"no", "not", "without", "zero", "बिना"}
_AFTER_NEG = {"nahi", "nahin", "nhi", "नहीं", "नही", "no"}
_PERSIST = ("ruk", "रुक", "stop")
_PUNCT = r"[,.;।!?]"


def _occurrences(text: str, kw: str):
    start = 0
    while True:
        i = text.find(kw, start)
        if i < 0:
            return
        if not (kw[0].isascii() and i > 0 and text[i - 1].isalpha()):
            yield i, i + len(kw)
        start = i + 1


def _negated(text: str, s: int, e: int) -> bool:
    before = re.split(_PUNCT, text[max(0, s - 30):s])[-1].split()[-2:]
    after_txt = re.split(_PUNCT, text[e:e + 30])[0]
    after = after_txt.split()[:2]
    if any(p in after_txt for p in _PERSIST):
        return False  # "khoon nahi ruk raha" = bleeding NOT stopping -> not a negation
    return any(w in _BEFORE_NEG for w in before) or any(w in _AFTER_NEG for w in after)


def extract_symptoms(text: str) -> list[str]:
    """Return symptom keys mentioned (and not simply negated) in text."""
    low = text.lower()
    found = []
    for key, spec in SYMPTOMS.items():
        for kw in spec["kw"]:
            if any(not _negated(low, s, e) for s, e in _occurrences(low, kw)):
                found.append(key)
                break
    return found


def check_danger(text: str, concerns=(), llm_candidates=(), lang: str = "en",
                 require_verified: bool = False) -> dict:
    """Independent of the LLM. LLM candidates only count if they are on the approved allowlist."""
    found: dict[str, str] = {}
    for k in extract_symptoms(text):
        if k in DANGER_RULES:
            found[k] = "rule"
    for k in concerns:
        if k in DANGER_RULES:
            found.setdefault(k, "rule")
    rejected = []
    for c in list(llm_candidates)[:10]:
        if isinstance(c, str) and c in DANGER_RULES:
            found.setdefault(c, "llm_allowlisted")
        else:
            rejected.append(str(c)[:40])
    signs = []
    lg = lang if lang in ("en", "hi") else "en"
    for k, by in found.items():
        rule = DANGER_RULES[k]
        if require_verified and not rule["verified"]:
            continue
        signs.append({"id": k, "label": SYMPTOMS[k][lg], "guideline": rule["source"],
                      "criteria": rule["criteria"], "verified": rule["verified"], "detected_by": by})
    return {"signs": signs, "rejected": rejected, "referral": referral(lang) if signs else None}
