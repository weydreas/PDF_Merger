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

    def test_endpoint_is_explicit_private_ipv4(self):
        self.assertEqual(pilot.private_endpoint("http://172.20.0.2:8080/v1"), "http://172.20.0.2:8080")
        for endpoint in ("", "https://api.openai.com:443/v1", "http://8.8.8.8:8080", "http://localhost:8080", "http://192.168.1.1:8080/other", "http://user:pass@192.168.1.1:8080"):
            with self.subTest(endpoint=endpoint), self.assertRaises(pilot.PilotError):
                pilot.private_endpoint(endpoint)

    def test_deployed_model_alias_requires_matching_metadata(self):
        item = {"id": "/models/model.gguf", "meta": {
            "n_ctx": 16384, "n_params": 2031739904, "size": 1276487680,
            "n_vocab": 151936, "n_embd": 2048, "ftype": "Q4_K - Medium",
        }}
        self.assertEqual(pilot.verified_model({"data": [item]}), item["id"])
        for key, value in (("n_ctx", 32768), ("n_params", 4000000000), ("size", 1)):
            wrong = copy.deepcopy(item)
            wrong["meta"][key] = value
            with self.subTest(key=key), self.assertRaises(pilot.PilotError):
                pilot.verified_model({"data": [wrong]})

    def test_split_redaction_scores_misses_and_review(self):
        reply = {"redact_ids": ["p1-r1", "p1-r2", "p1-r5"], "review_ids": ["p1-r6"]}
        pilot.validate_split("redaction", reply, self.regions)
        score = pilot.score_split("redaction", reply, self.fixture)
        self.assertEqual(score["missed_masks"], ["p1-r6"])
        self.assertFalse(score["pass"])

    def test_split_rejects_duplicate_and_unknown_ids(self):
        for reply in (
            {"redact_ids": ["p1-r1"], "review_ids": ["p1-r1"]},
            {"redact_ids": ["p9-r1"], "review_ids": []},
        ):
            with self.assertRaises(pilot.PilotError):
                pilot.validate_split("redaction", reply, self.regions)

    def test_split_new_fixture_date_and_identifiers(self):
        fixture = pilot.EXTRA_FIXTURES["identifiers"]
        regions = pilot.regions_for(fixture)
        masks = {"redact_ids": fixture["expected_redact"], "review_ids": []}
        date_reply = {"date": "2026-11-23", "date_evidence": "p1-r5"}
        for task, reply in (("redaction", masks), ("date", date_reply)):
            pilot.validate_split(task, reply, regions)
            self.assertTrue(pilot.score_split(task, reply, fixture)["pass"])

    def test_split_category_checks_study_title(self):
        fixture = pilot.FIXTURES["study"]
        reply = {"category": "study", "title": "УЗИ", "category_evidence": ["p1-r3"]}
        pilot.validate_split("classification", reply, pilot.regions_for(fixture))
        self.assertFalse(pilot.score_split("classification", reply, fixture)["pass"])
        reply["title"] = "Рентгенография грудной клетки"
        self.assertTrue(pilot.score_split("classification", reply, fixture)["pass"])

    def test_split_requests_are_independent(self):
        import contextlib
        import io
        with patch.object(pilot, "split_request", side_effect=[
            {"choices": [{"finish_reason": "stop", "message": {"content": "not json"}}]},
            {"choices": [{"finish_reason": "stop", "message": {"content": '{"category":"lab","title":null,"category_evidence":["p1-r4"]}'}}]},
            {"choices": [{"finish_reason": "stop", "message": {"content": '{"date":"2026-11-25","date_evidence":"p1-r3"}'}}]},
        ]) as request, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(pilot.run_split("synthetic", ["lab"]), 1)
        self.assertEqual(request.call_count, 3)
        self.assertIn('"classification": {"passed": 1, "total": 1}', output.getvalue())
        self.assertIn('"date": {"passed": 1, "total": 1}', output.getvalue())

    def test_truncated_reply_rejected(self):
        with patch.object(pilot, "request_json", return_value={"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}):
            with self.assertRaises(pilot.PilotError):
                pilot.infer(self.regions, "qwen-1.7b")


if __name__ == "__main__":
    unittest.main()
