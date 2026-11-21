"""Shared test helpers (stdlib only so tests run with unittest or pytest)."""
import asyncio
import base64
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.fernet import Fernet  # noqa: E402

from backend.config import load_config  # noqa: E402
from backend.gnani_adapter import MockSTT, MockTTS  # noqa: E402
from backend.session import Connection, build_state  # noqa: E402

KEY = Fernet.generate_key().decode()
PCM_100MS = base64.b64encode(b"\x01\x00" * 1600).decode()


def make_state(tmpdir, **kw):
    cfg = load_config({"VFR_ENCRYPTION_KEY": KEY, "VFR_DB_PATH": os.path.join(tmpdir, "t.db"), "LLM_PROVIDER": "mock"})
    return build_state(cfg, **kw)


class Client:
    """Drives a Connection and records everything the server sends."""

    def __init__(self, state, yield_on_send=False):
        self.out = []
        self.yield_on_send = yield_on_send
        self.conn = Connection(state, self._send)

    async def _send(self, m):
        self.out.append(m)
        if self.yield_on_send:
            await asyncio.sleep(0)

    async def say(self, text=None, lang="en"):
        """Send one utterance (typed fallback) and return (update, audio_frames)."""
        n = len(self.out)
        await self.conn.handle({"type": "end_utterance", "text": text})
        new = self.out[n:]
        upd = [m for m in new if m["type"] == "update"]
        return (upd[-1] if upd else None), [m for m in new if m["type"] == "audio"]

    def of(self, kind):
        return [m for m in self.out if m["type"] == kind]


def tmp():
    d = tempfile.TemporaryDirectory()
    return d
