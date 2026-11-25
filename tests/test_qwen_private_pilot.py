import copy
import unittest
from unittest.mock import patch

from scripts import qwen_private_pilot as pilot


class PrivateQwenPilotTests(unittest.TestCase):
    def setUp(self):
        self.fixture = pilot.FIXTURES["lab"]
        self.regions = pilot.regions_for(self.fixture)
        self.reply = {
            "regions": [{"id": r["id"], "action": "redact" if r["id"] in self.fixture["expected_redact"] else "keep"} for r in self.regions],
            "category": "lab",
            "category_evidence": ["p1-r4"],
            "title": None,
            "date": "2026-11-25",
            "date_evidence": "p1-r3",
        }

    def test_valid_answer_and_metrics(self):
        self.assertEqual(pilot.validate(self.reply, self.regions), self.reply)
        self.assertEqual(pilot.assess(self.fixture, self.reply), {
            "missed_masks": [], "extra_masks": [], "review_regions": [],
            "category_correct": True, "date_correct": True,
        })

    def test_missing_duplicate_unknown_id(self):
        for mutation in ("missing", "duplicate", "unknown"):
            answer = copy.deepcopy(self.reply)
            if mutation == "missing":
                answer["regions"].pop()
            else:
                answer["regions"][0]["id"] = answer["regions"][1]["id"] if mutation == "duplicate" else "p99-r1"
            with self.subTest(mutation=mutation), self.assertRaises(pilot.PilotError):
                pilot.validate(answer, self.regions)

    def test_birth_date_and_wrong_evidence_are_rejected(self):
        for day, source in [("1980-01-01", "p1-r2"), ("2026-11-24", "p1-r3"), ("2026-02-30", "p1-r3")]:
            answer = dict(self.reply, date=day, date_evidence=source)
            with self.subTest(day=day), self.assertRaises(pilot.PilotError):
                pilot.validate(answer, self.regions)

    def test_schema_does_not_prove_anonymization(self):
        answer = copy.deepcopy(self.reply)
        answer["regions"][0]["action"] = "keep"
        pilot.validate(answer, self.regions)
        self.assertEqual(pilot.assess(self.fixture, answer)["missed_masks"], ["p1-r1"])

    def test_unique_region_ids_across_pages(self):
        ids = [r["id"] for r in pilot.regions_for(pilot.FIXTURES["study"])]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("p2-r1", ids)

    def test_invalid_route_rejected_without_network(self):
        with patch("urllib.request.build_opener") as opener:
            with self.assertRaises(pilot.PilotError):
                pilot.request_json("https://example.com")
            opener.assert_not_called()

    def test_truncated_reply_rejected(self):
        with patch.object(pilot, "request_json", return_value={"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}):
            with self.assertRaises(pilot.PilotError):
                pilot.infer(self.regions, "qwen-4b")


if __name__ == "__main__":
    unittest.main()
