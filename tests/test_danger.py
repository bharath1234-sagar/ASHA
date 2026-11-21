import unittest

from tests import _util  # noqa: F401
from backend.danger import APPROVED_DANGER_KEYS, DANGER_RULES, check_danger, extract_symptoms


class DangerTests(unittest.TestCase):
    def test_detects_without_any_llm(self):
        r = check_danger("she has severe headache and blurred vision")
        self.assertEqual({s["id"] for s in r["signs"]}, {"severe_headache", "blurred_vision"})
        self.assertIn("health facility", r["referral"])

    def test_hindi_and_romanised(self):
        self.assertIn("convulsions", [s["id"] for s in check_danger("उसे दौरे पड़े")["signs"]])
        self.assertIn("heavy_bleeding", [s["id"] for s in check_danger("bahut zyada khoon")["signs"]])

    def test_negation_does_not_flag(self):
        self.assertEqual(check_danger("no bleeding, no problem")["signs"], [])
        self.assertEqual(check_danger("bleeding nahi hai")["signs"], [])

    def test_persisting_bleeding_is_not_negated(self):
        self.assertTrue(check_danger("khoon nahi ruk raha bleeding")["signs"])

    def test_negation_in_earlier_clause_does_not_hide_danger(self):
        self.assertTrue(check_danger("koi problem nahi, bleeding hai")["signs"])

    def test_llm_candidates_validated_against_allowlist(self):
        r = check_danger("routine visit", llm_candidates=["convulsions", "appendicitis", "give paracetamol"])
        self.assertEqual([s["id"] for s in r["signs"]], ["convulsions"])
        self.assertEqual(sorted(r["rejected"]), ["appendicitis", "give paracetamol"])

    def test_non_danger_concern_never_warns(self):
        self.assertEqual(check_danger("mild cough and weakness")["signs"], [])
        self.assertIn("cough", extract_symptoms("mild cough"))

    def test_unverified_rules_are_labelled_and_can_be_disabled(self):
        self.assertTrue(all(not DANGER_RULES[k]["verified"] for k in APPROVED_DANGER_KEYS))
        r = check_danger("convulsions", require_verified=True)
        self.assertEqual(r["signs"], [])
        s = check_danger("convulsions")["signs"][0]
        self.assertIn("PENDING VERIFICATION", s["guideline"])

    def test_no_numeric_thresholds_or_advice_in_phrases(self):
        from backend.phrases import REFERRAL
        for text in REFERRAL.values():
            self.assertFalse(any(c.isdigit() for c in text))
            for bad in ("mg", "tablet", "dose", "diagnos"):
                self.assertNotIn(bad, text.lower())


if __name__ == "__main__":
    unittest.main()
