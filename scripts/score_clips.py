"""Score the synthetic code-mixed transcript fixtures in assets/expected_cases.json.

These are TEXT fixtures (no audio clips were available). Exercises extraction + identity + danger.
  python scripts/score_clips.py              # rule-based extractor (offline)
  python scripts/score_clips.py --llm gemini # needs GEMINI_API_KEY in .env (not run during development)
"""
import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
from cryptography.fernet import Fernet  # noqa: E402
from backend.config import load_config  # noqa: E402
from backend.session import build_state  # noqa: E402
from backend.dialogue import DialogueSession  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--llm", choices=["mock", "gemini"], default="mock")
a = ap.parse_args()

tmp = tempfile.mkdtemp()
env = {"VFR_ENCRYPTION_KEY": Fernet.generate_key().decode(), "VFR_DB_PATH": str(Path(tmp) / "score.db"), "LLM_PROVIDER": a.llm}
if a.llm == "gemini":
    import os
    from dotenv import load_dotenv
    load_dotenv(root / ".env")
    env["GEMINI_API_KEY"] = os.environ.get("GEMINI_API_KEY", "")
state = build_state(load_config(env))
cases = json.load(open(root / "assets" / "expected_cases.json", encoding="utf-8"))["cases"]


async def main():
    ok = 0
    for c in cases:
        s = DialogueSession("score", "en")
        u = await state.engine.process(s, c["text"])
        e, problems = c["expect"], []
        if u["form"]["beneficiary_id"] != e["beneficiary_id"]:
            problems.append("beneficiary")
        for k in ("visit_type", "concerns", "follow_up_days"):
            if k in e and u["form"][k] != e[k]:
                problems.append(k)
        if sorted(x["id"] for x in u["danger"]["signs"]) != sorted(e["danger"]):
            problems.append("danger")
        ok += not problems
        print(("PASS " if not problems else "FAIL ") + c["id"] + ("" if not problems else "  -> " + ", ".join(problems)))
    print(f"\n{ok}/{len(cases)} cases passed (extractor: {state.llm.name})")


asyncio.run(main())
