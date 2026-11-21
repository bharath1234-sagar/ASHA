import json
import os
import unittest

from tests import _util
from tests._util import Client, make_state
from backend.llm import LLMError, validate_extraction


def run(coro):
    import asyncio
    return asyncio.run(coro)


class BrokenLLM:
    name = "broken"

    async def extract(self, text):
        raise LLMError("down")


class GarbageLLM:
    name = "garbage"

    async def extract(self, text):
        return {"visit_type": "cancer", "concerns": "lots"}


async def started(state, lang="en"):
    c = Client(state)
    await c.conn.handle({"type": "start", "language": lang})
    return c


class DialogueTests(unittest.TestCase):
    def setUp(self):
        self._d = _util.tmp()
        self.state = make_state(self._d.name)

    def tearDown(self):
        self._d.cleanup()

    def test_extraction_and_readback_before_save(self):
        async def go():
            c = await started(self.state)
            u, _ = await c.say("I visited Sunita, pregnancy checkup, no problem, follow up in 7 days")
            self.assertEqual(u["state"], "await_confirm")
            self.assertIn("Shall I save it", u["reply"])
            self.assertEqual(u["form"]["beneficiary_id"], "PAT003")
            self.assertEqual(u["form"]["follow_up_days"], 7)
            self.assertEqual(self.state.db.count(), 0)   # nothing saved before confirmation
        run(go())

    def test_missing_info_triggers_questions(self):
        async def go():
            c = await started(self.state)
            u, _ = await c.say("Today I visited Sunita")
            self.assertEqual(u["missing"], ["visit_type", "concerns"])
            self.assertIn("What kind of visit", u["reply"])
            u, _ = await c.say("pregnancy checkup")
            self.assertEqual(u["missing"], ["concerns"])
            self.assertIn("health problems", u["reply"])
            u, _ = await c.say("no problem")
            self.assertEqual(u["missing"], [])
        run(go())

    def test_correction_during_readback(self):
        async def go():
            c = await started(self.state)
            await c.say("Sunita pregnancy checkup no problem")
            u, _ = await c.say("no, it was a child checkup")
            self.assertEqual(u["form"]["visit_type"], "child")
            self.assertEqual(u["state"], "await_confirm")
            self.assertIn("child checkup", u["reply"])
            self.assertEqual(self.state.db.count(), 0)
            u, _ = await c.say("actually Meena not Sunita")
            self.assertEqual(u["form"]["beneficiary_id"], "PAT004")
        run(go())

    def test_yes_saves_once_no_does_not_save(self):
        async def go():
            c = await started(self.state)
            await c.say("Sunita pregnancy checkup no problem")
            u, _ = await c.say("no")
            self.assertEqual((u["state"], self.state.db.count()), ("collecting", 0))
            await c.say("no problem")           # back to readback
            u, _ = await c.say("yes")
            self.assertEqual((u["state"], self.state.db.count()), ("saved", 1))
            u, _ = await c.say("yes")           # repeated yes must not duplicate
            self.assertEqual(self.state.db.count(), 1)
            self.assertIn("already saved", u["reply"])
        run(go())

    def test_duplicate_name_never_guessed(self):
        async def go():
            c = await started(self.state)
            u, _ = await c.say("Today I visited Bharath.")
            self.assertIsNone(u["form"]["beneficiary_id"])
            self.assertIn("I found multiple beneficiaries with this name. Please provide the beneficiary ID.", u["reply"])
            self.assertEqual({x["id"] for x in u["candidates"]}, {"PAT001", "PAT002"})
            for cand in u["candidates"]:         # only ID + village, no health data
                self.assertEqual(set(cand), {"id", "village"})
            u, _ = await c.say("PAT002")
            self.assertEqual(u["form"]["beneficiary_id"], "PAT002")
            self.assertEqual(u["candidates"], [])
        run(go())

    def test_invalid_beneficiary_id(self):
        async def go():
            c = await started(self.state)
            await c.say("Today I visited Bharath.")
            u, _ = await c.say("PAT999")
            self.assertIsNone(u["form"]["beneficiary_id"])
            self.assertIn("not found in the register", u["reply"])
            self.assertNotIn("Demo Village", u["reply"])
        run(go())

    def test_readback_names_selected_id(self):
        async def go():
            c = await started(self.state)
            await c.say("Bharath")
            await c.say("pat zero zero one")
            u, _ = await c.say("pregnancy checkup, no problem")
            self.assertIn("ID PAT001", u["reply"])
        run(go())

    def test_llm_outage_falls_back_to_rules(self):
        async def go():
            st = make_state(self._d.name, llm=BrokenLLM())
            c = await started(st)
            u, _ = await c.say("Sunita pregnancy checkup no problem")
            self.assertIn("llm_unavailable_used_rules", u["warnings"])
            self.assertEqual(u["state"], "await_confirm")
        run(go())

    def test_invalid_llm_output_rejected(self):
        with self.assertRaises(ValueError):
            validate_extraction({"visit_type": "cancer"})
        async def go():
            st = make_state(self._d.name, llm=GarbageLLM())
            u, _ = await (await started(st)).say("Sunita pregnancy checkup no problem")
            self.assertEqual(u["form"]["visit_type"], "anc")
        run(go())

    def test_hindi_flow(self):
        async def go():
            c = await started(self.state, "hi")
            u, _ = await c.say("आज मैं सुनीता के घर गई थी, गर्भवती जाँच, कोई समस्या नहीं")
            self.assertEqual(u["state"], "await_confirm")
            self.assertIn("क्या इसे सेव करें", u["reply"])
            u, _ = await c.say("हाँ")
            self.assertEqual(u["state"], "saved")
        run(go())

    def test_synthetic_noisy_fixtures(self):
        path = os.path.join(os.path.dirname(__file__), "..", "assets", "expected_cases.json")
        cases = json.load(open(path, encoding="utf-8"))["cases"]
        async def go():
            for case in cases:
                c = await started(self.state)
                u, _ = await c.say(case["text"])
                exp = case["expect"]
                with self.subTest(case["id"]):
                    self.assertEqual(u["form"]["beneficiary_id"], exp["beneficiary_id"])
                    if "visit_type" in exp:
                        self.assertEqual(u["form"]["visit_type"], exp["visit_type"])
                    if "concerns" in exp:
                        self.assertEqual(u["form"]["concerns"], exp["concerns"])
                    if "follow_up_days" in exp:
                        self.assertEqual(u["form"]["follow_up_days"], exp["follow_up_days"])
                    if exp.get("needs_id"):
                        self.assertTrue(u["candidates"])
                    self.assertEqual(sorted(s["id"] for s in u["danger"]["signs"]), sorted(exp["danger"]))
        run(go())


if __name__ == "__main__":
    unittest.main()
