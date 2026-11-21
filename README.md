# Voice Field Register — Team Cookies

Voice-to-record visit register for ASHA workers (Gnani Voice Hackathon demo).
Speak a visit in Hindi/English → transcript → structured record → danger-sign check → read-back →
**explicit yes** → encrypted save → spoken reply.

## Read this first: what is real and what is not

| Part | Status |
|---|---|
| Dialogue, extraction, identity resolution, corrections, confirm-once save | Built and **tested** (mock + rule-based) |
| Danger-sign checker | Built and tested, but **rules are PENDING MEDICAL VERIFICATION** (no guideline could be checked) |
| Encrypted SQLite (Fernet), deletion script | Built and **tested** |
| Browser mic capture + 16 kHz PCM16 resampler | Built; resampler **tested in Node**; **not yet tried in a real browser** |
| FastAPI server + WebSocket wrapper (`backend/main.py`) | Built; **NOT executed** during development (FastAPI could not be installed there). Run the tests on your PC first |
| Mock mode (default) | Works with no keys. Mock STT does **not** understand audio; it plays a demo script. Mock TTS is a chime (+ browser voice) |
| **Live Gnani Prisma/Timbre** | **NOT implemented.** Official protocol was not available; nothing was invented. See `docs/integration_report.md` |
| Gemini extraction | Code written against the `google-genai` SDK; **never run** (no key/network). Falls back to rules on any failure |
| Beneficiary register | **Synthetic** (`assets/synthetic_beneficiaries.json`). No real database is connected |

No AI runs offline. The service worker only lets the page shell open without a connection.

## Setup on Windows 11 (PowerShell + VS Code)

Prerequisite: Python 3.10+ from python.org ("Add python.exe to PATH" ticked). Check: `py --version`

```powershell
# 1. Extract the ZIP (right-click > Extract All), then:
cd $HOME\Downloads\voice-field-register        # adjust to where you extracted it

# 2. Open in VS Code
code .

# 3. Create and activate a virtual environment (in the VS Code terminal)
py -m venv .venv
.\.venv\Scripts\Activate.ps1
#   If PowerShell says scripts are disabled, run once:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# 4. Install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt

# 5. Create .env with an encryption key (never overwrites an existing .env)
python scripts/setup_env.py

# 6. Run the automated tests FIRST (this is also the first real check of the FastAPI part)
python -m pytest -q
node tests/test_worklet.js        # optional, only if Node.js is installed

# 7. Start the backend
python -m backend.main
```

8. Open **http://localhost:8000** in Chrome or Edge (use `localhost`; microphones are blocked on plain http elsewhere).
   The badge should say **Connected** and a yellow "Demo mode" banner appears.
9. Click the big mic: the browser asks for microphone permission → **Allow**. Tap to start, tap again to stop.
10. Stop the server with `Ctrl+C`.

### Optional keys (`.env`, edit in VS Code)
- `GEMINI_API_KEY=` your key → real LLM extraction (otherwise offline rules are used). Transcript text is then sent to Google.
- `PROVIDER=gnani` + `GNANI_*` → only after the live adapter is completed (see integration report). Never put keys in the frontend or in chat.

## 3-minute synthetic demo (mock mode)
Each mic tap = "next demo line" (the script is shown in `backend/gnani_adapter.py`, English and Hindi):
1. "Today I visited Bharath." → *multiple beneficiaries, please give the ID* (IDs and village only).
2. "PAT002" → ID accepted, asks visit type.
3. "It was a pregnancy checkup." → asks for problems.
4. "She has severe headache and blurred vision, follow up in 3 days." → **red danger warning + fixed referral phrase**, then full read-back with ID.
5. "Yes" → saved once, follow-up list appears. Tapping yes again says "already saved".
You can also use the Yes/No buttons, or "Type instead", to show corrections ("no, it was a child checkup").
After judging: `python scripts/delete_all_data.py --yes`.

## Data protection
Encrypted with Fernet (key in `.env`): the form payload and the transcript. **Visible (plaintext) metadata:** row id, draft id,
session id, beneficiary ID, timestamp, danger flag. Logs never include transcripts or keys. The browser stores only a session id and language.
Back up `VFR_ENCRYPTION_KEY`; without it saved records are unreadable. The demo database holds synthetic data only.

## Known limitations
- Danger rules are qualitative, unverified, keyword-based; negation handling is simple. Not a clinical tool.
- Free-text concerns outside the fixed vocabulary are recorded only as "other (not itemised)".
- Mock STT ignores what is actually said. Only English/Hindi; more languages = add a phrase table in `phrases.py` + keywords.
- Sessions live in server memory (a server restart loses unsaved drafts). Saved records are safe in SQLite.
- Spoken digit words are normalised for English only ("pat zero zero two").
- No authentication; single-user demo.

## Layout
`backend/` server, dialogue, LLM, danger, db · `frontend/` PWA · `tests/` · `scripts/` · `assets/` · `docs/`
