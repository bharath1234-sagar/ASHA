# Integration report

**Inputs actually available when this was built:** only the master prompt. The hackathon PDF, MASTER SPEC v2.0 and the existing GitHub repo were **not** provided, so the WebSocket contract and module interfaces were derived from the prompt, and no existing repo features were preserved or compared. If your spec differs, `docs/architecture.md` lists every message and field to reconcile.

## Team status
| Team | Delivered | Status |
|---|---|---|
| A | session.py/main.py WebSocket + lifecycle, interruption, error codes, provider adapters, mock STT/TTS | Logic tested; FastAPI wrapper **not executed**; **live Gnani NOT implemented** |
| B | dialogue state machine, validated extraction, Gemini client, identity resolution, corrections, read-back, follow-ups, fixtures + scorer | Tested with mock/rule extractor; Gemini **not run** |
| C | PWA, AudioWorklet resampler, WebSocket client, playback, barge-in, reconnect, a11y, SW | Resampler tested in Node; rest **not tested in a browser** |
| D | danger rules, fixed phrases, encrypted DB, deletion, docs | Tested; danger rules **unverified (blocker)** |

Cross-team changes: session.py and config.py were added (not in the ownership list); `BeneficiaryRegister` lives in db.py (Team D file) but is used by Team B; mock STT script lives in gnani_adapter.py; phrases.py also holds dialogue prompt text (Team B content).

## Blockers
1. **Gnani live mode.** Needed from the official docs / organiser sheet: STT WebSocket URL; auth header name/format; session-start and audio-frame message format (is audio raw binary or base64 JSON?); partial/final transcript event names; language-code values for hi/en code-mixing; TTS URL, request format, output sample rate/encoding, and whether audio arrives as binary or JSON. Fill in `GnaniSTT.transcribe` / `GnaniTTS.stream` in `backend/gnani_adapter.py`, then set `PROVIDER=gnani`. Test with real audio before claiming integration.
2. **Danger-sign guideline verification.** `backend/danger.py` rules are qualitative candidates with `verified=False` and source "PENDING VERIFICATION". A medical reviewer must supply the guideline document/edition/page per sign. No numeric thresholds were used.
3. **Gemini.** Confirm the model name in `GEMINI_MODEL` is available to your key; run `python scripts/score_clips.py --llm gemini`.
4. **First run of FastAPI + browser** on your machine (see README step 6).
