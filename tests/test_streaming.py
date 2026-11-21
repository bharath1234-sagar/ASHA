import asyncio
import base64
import unittest

from tests import _util
from tests._util import Client, PCM_100MS, make_state
from backend.gnani_adapter import MockSTT, MockTTS, STTError, TTSError


def run(coro):
    return asyncio.run(coro)


class DownSTT:
    name = "down"

    async def transcribe(self, pcm, lang, ctx):
        raise ConnectionError("network down")


class DownTTS:
    name = "down"
    sample_rate = 16000

    async def stream(self, text, lang):
        raise TTSError("down")
        yield b""


class StreamingTests(unittest.TestCase):
    def setUp(self):
        self._d = _util.tmp()
        self.state = make_state(self._d.name)

    def tearDown(self):
        self._d.cleanup()

    def test_start_returns_ready_with_audio_contract(self):
        async def go():
            c = Client(self.state)
            await c.conn.handle({"type": "start", "language": "en"})
            r = c.of("ready")[0]
            self.assertEqual(r["audio"], {"format": "pcm_s16le", "sample_rate": 16000, "channels": 1, "chunk_ms": 100})
            self.assertFalse(r["resumed"])
            self.assertEqual(r["mode"], "mock")
        run(go())

    def test_requires_start_and_rejects_bad_language_and_unknown_type(self):
        async def go():
            c = Client(self.state)
            await c.conn.handle({"type": "audio", "data": PCM_100MS})
            self.assertEqual(c.of("error")[-1]["code"], "no_session")
            await c.conn.handle({"type": "start", "language": "xx"})
            self.assertEqual(c.of("error")[-1]["code"], "invalid_language")
            await c.conn.handle({"type": "start", "language": "en"})
            await c.conn.handle({"type": "bogus"})
            self.assertEqual(c.of("error")[-1]["code"], "unknown_message_type")
        run(go())

    def test_audio_validation(self):
        async def go():
            c = Client(self.state)
            await c.conn.handle({"type": "start", "language": "en"})
            await c.conn.handle({"type": "audio", "data": "!!!notbase64"})
            self.assertEqual(c.of("error")[-1]["code"], "bad_audio")
            await c.conn.handle({"type": "audio", "data": base64.b64encode(b"\x00\x01\x02").decode()})
            self.assertEqual(c.of("error")[-1]["code"], "bad_audio")   # odd byte count is not PCM16
        run(go())

    def test_mock_stt_voice_to_response_workflow(self):
        async def go():
            c = Client(self.state)
            await c.conn.handle({"type": "start", "language": "en"})
            await c.conn.handle({"type": "audio", "data": PCM_100MS})
            await c.conn.handle({"type": "end_utterance"})
            self.assertEqual(c.of("transcript")[0]["text"], "Today I visited Bharath.")
            self.assertIn("multiple beneficiaries", c.of("update")[-1]["reply"])
            frames = c.of("audio")
            self.assertTrue(frames[-1]["final"])
            pcm = b"".join(base64.b64decode(f["data"]) for f in frames)
            self.assertTrue(len(pcm) > 0 and len(pcm) % 2 == 0)
        run(go())

    def test_mock_stt_no_audio_error(self):
        async def go():
            c = Client(self.state)
            await c.conn.handle({"type": "start", "language": "en"})
            await c.conn.handle({"type": "end_utterance"})
            self.assertEqual(c.of("error")[-1]["code"], "no_audio")
        run(go())

    def test_mock_tts_output_is_chunked_pcm16_16k(self):
        async def go():
            chunks = [x async for x in MockTTS(0.5).stream("hello", "en")]
            self.assertTrue(all(len(x) <= 3200 and len(x) % 2 == 0 for x in chunks))
            self.assertEqual(sum(len(x) for x in chunks), 16000)  # 0.5 s * 16000 samples * 2 bytes / 2 ... = 16000 bytes
        run(go())

    def test_stt_outage_reports_error_and_typed_fallback_still_works(self):
        async def go():
            st = make_state(self._d.name, stt=DownSTT())
            c = Client(st)
            await c.conn.handle({"type": "start", "language": "en"})
            await c.conn.handle({"type": "audio", "data": PCM_100MS})
            await c.conn.handle({"type": "end_utterance"})
            e = c.of("error")[-1]
            self.assertEqual((e["code"], e["retryable"]), ("stt_unavailable", True))
            u, _ = await c.say("Sunita pregnancy checkup no problem")
            self.assertEqual(u["state"], "await_confirm")
        run(go())

    def test_tts_outage_still_delivers_text_update(self):
        async def go():
            st = make_state(self._d.name, tts=DownTTS())
            c = Client(st)
            await c.conn.handle({"type": "start", "language": "en"})
            u, _ = await c.say("Today I visited Bharath.")
            self.assertIn("multiple beneficiaries", u["reply"])
            self.assertEqual(c.of("error")[-1]["code"], "tts_unavailable")
        run(go())

    def test_interrupt_stops_audio_stream(self):
        async def go():
            st = make_state(self._d.name, tts=MockTTS(3.0))   # 30 chunks
            c = Client(st, yield_on_send=True)
            await c.conn.handle({"type": "start", "language": "en"})
            task = asyncio.create_task(c.conn.handle({"type": "end_utterance", "text": "Today I visited Bharath."}))
            while not [m for m in c.of("audio") if not m["final"]]:
                await asyncio.sleep(0)
            await c.conn.handle({"type": "interrupt"})
            await task
            frames = c.of("audio")
            self.assertTrue(frames[-1]["final"] and frames[-1]["interrupted"])
            self.assertLess(len([f for f in frames if not f["final"]]), 30)
        run(go())

    def test_stt_error_type_has_code(self):
        e = STTError("x", "y")
        self.assertEqual((e.code, e.message), ("x", "y"))


if __name__ == "__main__":
    unittest.main()
