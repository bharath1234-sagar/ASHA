import asyncio
import os
import sqlite3
import unittest

from tests import _util
from tests._util import Client, make_state
from backend.config import ConfigError, load_config

try:
    import fastapi  # noqa: F401
    from fastapi.testclient import TestClient
    HAVE_FASTAPI = True
except Exception:
    HAVE_FASTAPI = False


def run(coro):
    return asyncio.run(coro)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self._d = _util.tmp()
        self.state = make_state(self._d.name)

    def tearDown(self):
        self._d.cleanup()

    def test_config_errors_are_clear_and_secrets_hidden(self):
        with self.assertRaises(ConfigError) as cm:
            load_config({})
        self.assertIn("VFR_ENCRYPTION_KEY", str(cm.exception))
        with self.assertRaises(ConfigError) as cm:
            load_config({"VFR_ENCRYPTION_KEY": _util.KEY, "PROVIDER": "gnani"})
        self.assertIn("GNANI_API_KEY", str(cm.exception))
        cfg = load_config({"VFR_ENCRYPTION_KEY": _util.KEY, "GNANI_API_KEY": "SECRET123", "GEMINI_API_KEY": "SECRET456"})
        self.assertNotIn("SECRET123", repr(cfg))
        self.assertNotIn("SECRET456", repr(cfg))
        self.assertNotIn(_util.KEY, repr(cfg))

    def test_full_demo_script_end_to_end_with_encrypted_storage(self):
        async def go():
            c = Client(self.state)
            await c.conn.handle({"type": "start", "language": "en"})
            for _ in range(5):                     # the 5 scripted mock utterances
                await c.conn.handle({"type": "audio", "data": _util.PCM_100MS})
                await c.conn.handle({"type": "end_utterance"})
            last = c.of("update")[-1]
            self.assertEqual(last["state"], "saved")
            self.assertTrue(last["danger"]["signs"])
            self.assertEqual(last["followups"][0]["beneficiary_id"], "PAT002")
            self.assertEqual(self.state.db.count(), 1)
            rec = self.state.db.get_visit(last["saved_record_id"])
            self.assertEqual(rec["form"]["beneficiary_id"], "PAT002")
            self.assertIn("Today I visited Bharath.", rec["transcript"])
            # raw database file must not contain plaintext PHI
            raw = open(self.state.db.path, "rb").read()
            for secret in (b"Today I visited", b"PAT002\"", b"severe_headache", b"pregnancy"):
                self.assertNotIn(secret, raw)
            con = sqlite3.connect(self.state.db.path)
            blob = con.execute("SELECT payload_enc FROM visits").fetchone()[0]
            con.close()
            self.assertTrue(blob.startswith(b"gAAAA"))   # Fernet token
        run(go())

    def test_reconnect_resumes_and_never_duplicates(self):
        async def go():
            c1 = Client(self.state)
            await c1.conn.handle({"type": "start", "language": "en"})
            sid = c1.of("ready")[0]["session_id"]
            await c1.say("Sunita pregnancy checkup no problem")
            c2 = Client(self.state)                       # new socket, same session
            await c2.conn.handle({"type": "resume", "session_id": sid, "language": "en"})
            self.assertTrue(c2.of("ready")[0]["resumed"])
            self.assertEqual(c2.of("update")[0]["state"], "await_confirm")
            await c2.say("yes")
            c3 = Client(self.state)
            await c3.conn.handle({"type": "resume", "session_id": sid, "language": "en"})
            await c3.say("yes")
            self.assertEqual(self.state.db.count(), 1)
        run(go())

    def test_db_idempotent_on_draft_id(self):
        db = self.state.db
        a = db.save_visit("d1", "s", "PAT001", {"x": 1}, ["t"], False)
        b = db.save_visit("d1", "s", "PAT001", {"x": 1}, ["t"], False)
        self.assertEqual((a[1], b[1], a[0] == b[0], db.count()), (True, False, True, 1))

    def test_wrong_key_cannot_decrypt(self):
        from cryptography.fernet import Fernet, InvalidToken
        from backend.db import VisitDB
        rid, _ = self.state.db.save_visit("d2", "s", "PAT001", {"x": 1}, ["t"], False)
        other = VisitDB(self.state.db.path, Fernet.generate_key().decode())
        with self.assertRaises(InvalidToken):
            other.get_visit(rid)

    def test_data_deletion(self):
        db = self.state.db
        db.save_visit("d3", "s", "PAT001", {"x": 1}, ["secret text"], True)
        self.assertEqual(db.delete_all(), 1)
        self.assertEqual(db.count(), 0)
        db.save_visit("d4", "s", "PAT001", {"x": 1}, ["t"], False)
        db.delete_all(remove_file=True)
        self.assertFalse(os.path.exists(db.path))

    def test_frontend_uses_contract_message_types(self):
        root = os.path.dirname(os.path.dirname(__file__))
        js = open(os.path.join(root, "frontend", "app.js"), encoding="utf-8").read()
        for t in ("start", "resume", "audio", "interrupt", "end_utterance"):
            self.assertIn(f"type: '{t}'", js)
        for t in ("ready", "transcript", "audio", "update", "error"):
            self.assertIn(f"case '{t}'", js)
        self.assertNotIn("API_KEY", js)

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi not installed in this environment")
    def test_fastapi_startup_and_websocket(self):
        from backend.main import create_app
        app = create_app(self.state.cfg, self.state)
        with TestClient(app) as client:
            self.assertEqual(client.get("/health").json()["status"], "ok")
            self.assertEqual(client.get("/").status_code, 200)
            with client.websocket_connect("/ws") as ws:
                ws.send_json({"type": "start", "language": "en"})
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_json({"type": "end_utterance", "text": "Today I visited Bharath."})
                kinds = []
                while True:
                    m = ws.receive_json()
                    kinds.append(m["type"])
                    if m["type"] == "audio" and m.get("final"):
                        break
                self.assertIn("transcript", kinds)
                self.assertIn("update", kinds)


if __name__ == "__main__":
    unittest.main()
