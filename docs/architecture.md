# Architecture

```
mic → AudioWorklet (resample → mono PCM16 16 kHz, 100 ms) → WebSocket /ws
   → session.py (Connection) → STT provider → final transcript
   → dialogue.py → llm.py (validated JSON; rule fallback) + db.BeneficiaryRegister (identity)
   → danger.py (rules; LLM candidates allow-listed) → read-back → explicit yes
   → db.py (Fernet-encrypted SQLite, idempotent on draft_id)
   → TTS provider → audio frames (PCM16) → browser playback
```
`session.py` holds all protocol logic with no FastAPI import, so it is unit-tested directly; `main.py` only wires HTTP/WebSocket.

## WebSocket contract (JSON text frames)
Client → server
- `start {language, session_id?}` / `resume {session_id, language}` — both attach to an existing session if the id is known.
- `audio {data}` — base64 mono PCM16 LE 16 kHz, ≈100 ms (3200 bytes); no WAV header; ≤64 KB per frame.
- `end_utterance {text?}` — finish speaking. **Addition:** optional `text` = typed/button/demo input, bypasses STT.
- `interrupt` — stop reply audio and drop buffered mic audio.

Server → client
- `ready {session_id, resumed, language, mode, stt, tts, llm, audio:{format,sample_rate,channels,chunk_ms}}`
- `transcript {text, final}`
- `update {state, reply, form, missing, candidates, danger, saved_record_id, followups, warnings, lang}`
- `audio {data, seq, final, interrupted?, format, sample_rate, channels, tts, text?, lang?}` — **Addition:** metadata fields; final frame has empty `data`.
- `error {code, message, retryable}` — codes: no_session, invalid_language, bad_audio, buffer_overflow, no_audio, no_speech, stt_unavailable, gnani_not_implemented, tts_unavailable, bad_json, unknown_message_type, internal_error.

## Dialogue states
`collecting → await_confirm → saved`. Only "yes" (with no new fields in the same utterance) in `await_confirm` saves. "No" returns to `collecting`.
A new visit starts automatically when the user speaks new content after `saved`. Danger signs are sticky for the visit.

## Safe duplicate handling
Each visit has a `draft_id`; the DB column is UNIQUE and written with `ON CONFLICT DO NOTHING`. Reconnecting resumes the same server session, so a repeated "yes" cannot create a second row.
