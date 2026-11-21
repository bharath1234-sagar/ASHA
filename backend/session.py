"""WebSocket session logic (Team A), independent of FastAPI so it can be tested directly.

Contract (client -> server): start, resume, audio, interrupt, end_utterance
                (server -> client): ready, transcript, audio, update, error
Additions to the base contract (documented in docs/architecture.md):
  - end_utterance may carry optional "text": typed/demo input that bypasses STT.
  - audio frames carry seq/final/interrupted/sample_rate/tts metadata.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from .config import Config
from .db import BeneficiaryRegister, VisitDB
from .dialogue import DialogueEngine, SessionStore
from .gnani_adapter import CHUNK_BYTES, SAMPLE_RATE, STTError, TTSError, build_providers
from .llm import build_llm
from .phrases import SUPPORTED_LANGS

log = logging.getLogger("vfr")
MAX_AUDIO_MSG_BYTES = 64_000
MAX_BUFFER_BYTES = SAMPLE_RATE * 2 * 60   # 60 s of utterance


@dataclass
class AppState:
    cfg: Config
    register: BeneficiaryRegister
    db: VisitDB
    llm: object
    engine: DialogueEngine
    stt: object
    tts: object
    sessions: SessionStore


def build_state(cfg: Config, llm=None, stt=None, tts=None) -> AppState:
    register = BeneficiaryRegister(cfg.beneficiaries_path)
    db = VisitDB(cfg.db_path, cfg.encryption_key)
    llm = llm or build_llm(cfg)
    d_stt, d_tts = build_providers(cfg)
    engine = DialogueEngine(register, db, llm, cfg.require_verified_danger)
    return AppState(cfg, register, db, llm, engine, stt or d_stt, tts or d_tts, SessionStore())


class Connection:
    def __init__(self, app: AppState, send: Callable[[dict], Awaitable[None]]):
        self.app, self._send = app, send
        self.sess = None
        self.audio = bytearray()
        self.interrupted = False
        self.lock = asyncio.Lock()

    async def send(self, msg: dict) -> None:
        await self._send(msg)

    async def error(self, code: str, message: str, retryable: bool = True) -> None:
        await self.send({"type": "error", "code": code, "message": message, "retryable": retryable})

    async def handle(self, msg: dict) -> None:
        try:
            await self._dispatch(msg)
        except Exception as e:  # never leak payloads; log only the exception type
            log.error("internal error: %s", type(e).__name__)
            try:
                await self.error("internal_error", "Something went wrong. Please try again.")
            except Exception:
                pass

    async def _dispatch(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind in ("start", "resume"):
            return await self._start(msg)
        if kind == "interrupt":
            self.interrupted = True
            self.audio.clear()
            return
        if self.sess is None:
            return await self.error("no_session", "Send start first.", retryable=False)
        if kind == "audio":
            return await self._audio(msg)
        if kind == "end_utterance":
            return await self._end_utterance(msg)
        await self.error("unknown_message_type", "Unknown message type.", retryable=False)

    async def _start(self, msg: dict) -> None:
        lang = msg.get("language") or "hi"
        if lang not in SUPPORTED_LANGS:
            return await self.error("invalid_language", f"Supported languages: {', '.join(SUPPORTED_LANGS)}", False)
        sess = self.app.sessions.get(msg.get("session_id")) if msg.get("session_id") else None
        resumed = sess is not None
        if sess is None:
            sess = self.app.sessions.create(lang)
        sess.lang = lang
        self.sess = sess
        self.audio.clear()
        self.interrupted = False
        await self.send({"type": "ready", "session_id": sess.session_id, "resumed": resumed, "language": lang,
                         "mode": self.app.cfg.provider, "stt": self.app.stt.name, "tts": self.app.tts.name,
                         "llm": self.app.llm.name,
                         "audio": {"format": "pcm_s16le", "sample_rate": SAMPLE_RATE, "channels": 1, "chunk_ms": 100}})
        if resumed:
            await self.send({"type": "update", **self.app.engine.snapshot(sess)})

    async def _audio(self, msg: dict) -> None:
        try:
            raw = base64.b64decode(msg.get("data") or "", validate=True)
        except (binascii.Error, ValueError):
            return await self.error("bad_audio", "Audio chunk is not valid base64.")
        if not raw or len(raw) % 2 or len(raw) > MAX_AUDIO_MSG_BYTES:
            return await self.error("bad_audio", "Audio must be mono PCM16 (even byte count, under 64 KB per chunk).")
        if len(self.audio) + len(raw) > MAX_BUFFER_BYTES:
            self.audio.clear()
            return await self.error("buffer_overflow", "Utterance too long. Please speak in shorter parts.")
        self.audio += raw

    async def _end_utterance(self, msg: dict) -> None:
        async with self.lock:
            self.interrupted = False
            pcm, self.audio = bytes(self.audio), bytearray()
            sess = self.sess
            typed = msg.get("text")
            if isinstance(typed, str) and typed.strip():
                text = typed.strip()[:500]
            else:
                try:
                    text = await self.app.stt.transcribe(pcm, sess.lang, sess.ctx)
                except STTError as e:
                    return await self.error(e.code, e.message)
                except Exception:
                    return await self.error("stt_unavailable", "Speech recognition is unavailable. You can use the typed fallback.")
            if not text.strip():
                return await self.error("no_speech", "Nothing was recognised. Please try again.")
            await self.send({"type": "transcript", "text": text, "final": True})
            result = await self.app.engine.process(sess, text)
            await self.send({"type": "update", **result})
            await self._speak(result["reply"], sess.lang)

    async def _speak(self, text: str, lang: str) -> None:
        if not text:
            return
        seq = 0
        tts = self.app.tts
        try:
            async for chunk in tts.stream(text, lang):
                if self.interrupted:
                    break
                await self.send({"type": "audio", "data": base64.b64encode(chunk).decode("ascii"), "seq": seq,
                                 "final": False, "format": "pcm_s16le", "sample_rate": tts.sample_rate,
                                 "channels": 1, "tts": tts.name,
                                 **({"text": text, "lang": lang} if seq == 0 else {})})
                seq += 1
        except Exception:
            await self.error("tts_unavailable", "Voice reply is unavailable. Read the reply on screen.", retryable=False)
        await self.send({"type": "audio", "data": "", "seq": seq, "final": True, "interrupted": self.interrupted,
                         "format": "pcm_s16le", "sample_rate": tts.sample_rate, "channels": 1, "tts": tts.name})
