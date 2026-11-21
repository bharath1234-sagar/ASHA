"""Speech adapters for mock mode and Gnani Prisma/Timbre v3."""
from __future__ import annotations

import asyncio
import math
import struct
from typing import AsyncIterator

SAMPLE_RATE = 16000
CHUNK_BYTES = 3200
GNANI_FRAME_BYTES = 1024


class STTError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class TTSError(Exception):
    pass


MOCK_SCRIPT = {
    "en": [
        "Today I visited Bharath.",
        "PAT002",
        "It was a pregnancy checkup.",
        "She has severe headache and blurred vision, follow up in 3 days.",
        "Yes",
    ],
    "hi": [
        "आज मैं भरत के घर गई थी।",
        "PAT002",
        "गर्भावस्था जाँच थी।",
        "तेज सिरदर्द और धुंधला दिखना है, 3 दिन बाद फॉलो-अप।",
        "हाँ",
    ],
}


class MockSTT:
    name = "mock"

    async def transcribe(self, pcm: bytes, lang: str, ctx: dict) -> str:
        if len(pcm) < CHUNK_BYTES:
            raise STTError(
                "no_audio",
                "No audio was received. Hold the microphone for a little longer.",
            )
        lines = MOCK_SCRIPT.get(lang, MOCK_SCRIPT["en"])
        index = ctx.get("mock_idx", 0)
        ctx["mock_idx"] = index + 1
        return lines[index % len(lines)]


class MockTTS:
    name = "mock"
    sample_rate = SAMPLE_RATE

    def __init__(self, duration_s: float = 0.5):
        self.duration_s = duration_s

    async def stream(self, text: str, lang: str) -> AsyncIterator[bytes]:
        count = int(SAMPLE_RATE * self.duration_s)
        samples = bytearray()
        for i in range(count):
            envelope = min(1.0, i / 800, (count - i) / 800)
            sample = int(
                9000 * envelope
                * math.sin(2 * math.pi * 523.25 * i / SAMPLE_RATE)
            )
            samples += struct.pack("<h", sample)

        for offset in range(0, len(samples), CHUNK_BYTES):
            yield bytes(samples[offset:offset + CHUNK_BYTES])
            await asyncio.sleep(0)


async def _pcm_frames(pcm: bytes):
    """Yield exact 1024-byte frames required by the Gnani streaming SDK."""
    usable = pcm[:len(pcm) - (len(pcm) % 2)]
    for offset in range(0, len(usable), GNANI_FRAME_BYTES):
        frame = usable[offset:offset + GNANI_FRAME_BYTES]
        if len(frame) < GNANI_FRAME_BYTES:
            frame += b"\x00" * (GNANI_FRAME_BYTES - len(frame))
        yield frame


class GnaniSTT:
    name = "gnani-prisma-v3.0"
    
    def __init__(self, api_key: str, base_url: str):
        self._api_key = api_key
        self._base_url = base_url

    async def transcribe(self, pcm: bytes, lang: str, ctx: dict) -> str:
        if not pcm or len(pcm) < GNANI_FRAME_BYTES:
            raise STTError("no_audio", "No usable audio was received.")

        # Append 1 second of PCM16 silence so Prisma can detect
        # the end of the utterance before the SDK closes the stream.
        pcm = pcm + (b"\x00" * SAMPLE_RATE * 2)

        try:
            from gnani.stt import GnaniSTTStreamClient

            client = GnaniSTTStreamClient(
                api_key=self._api_key,
                language_code="indic-auto",
                model="prisma-v3.0",
                sample_rate=SAMPLE_RATE,
                format="codemix",
                base_url=self._base_url,
            )

            await client.connect()
            try:
                events = await client.stream_audio(
                    _pcm_frames(pcm),
                    chunk_size=GNANI_FRAME_BYTES,
                    realtime_pace=True,
                )
            except Exception:
                try:
                    await client.close()
                except Exception:
                    pass
                raise

            segments = []
            for event in events or []:
                segment = getattr(event, "text", "")
                if segment and str(segment).strip():
                    segments.append(str(segment).strip())

            transcript = " ".join(segments).strip()
            if not transcript:
                raise STTError(
                    "no_transcript",
                    "Gnani did not return a transcript. Please try speaking again.",
                )
            return transcript

        except STTError:
            raise
        except Exception as exc:
            raise STTError(
                "gnani_stt_error",
                "Gnani speech recognition failed. Check the connection and API configuration. "
                f"Error type: {type(exc).__name__}.",
            ) from None


class GnaniTTS:
    name = "gnani-timbre-v3.0"
    sample_rate = SAMPLE_RATE

    def __init__(self, api_key: str, base_url: str):
        self._api_key = api_key
        self._base_url = base_url

    async def stream(self, text: str, lang: str) -> AsyncIterator[bytes]:
        if not text or not text.strip():
            return

        language_code = {
            "hi": "hi-IN",
            "en": "en-IN",
        }.get(lang, "en-IN")

        try:
            from gnani.tts import GnaniTTSRealtimeClient, AudioConfig

            client = GnaniTTSRealtimeClient(
                api_key=self._api_key,
                base_url=self._base_url,
            )
            audio_config = AudioConfig(
                sample_rate=SAMPLE_RATE,
                encoding="linear_pcm",
                container="raw",
            )

            async for chunk in client.synthesize(
                text,
                voice="Avinash",
                model="timbre-v3.0",
                language=language_code,
                audio_config=audio_config,
            ):
                if chunk:
                    yield bytes(chunk)

        except Exception as exc:
            raise TTSError(
                "Gnani speech synthesis failed. "
                "Check the connection, voice availability and API configuration. "
                f"Error type: {type(exc).__name__}."
            ) from None


def build_providers(cfg):
    if cfg.provider == "gnani":
        if not cfg.gnani_api_key:
            raise RuntimeError("GNANI_API_KEY is required for Gnani mode.")
        return (
            GnaniSTT(cfg.gnani_api_key, cfg.gnani_base_url),
            GnaniTTS(cfg.gnani_api_key, cfg.gnani_base_url),
        )
    return MockSTT(), MockTTS()
