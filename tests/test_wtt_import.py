"""Run: python3 -m unittest discover tests   (from the tt-tracker folder)

Fixtures are trimmed real GetMatchCardDetails responses from China Smash 2026 (event 3249).
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wtt_import import build_records, is_duplicate  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
EVENT = "China Smash 2026 Presented by Beijing Shijingshan Culture & Tourism Group"


def card(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def row(player, **kw):
    return {"player": player, "partner": "", "opponent": "X Y", "oppPartner": "", "date": "2020-01-01",
            "group": "podium", **kw}


class SinglesTest(unittest.TestCase):
    def test_away_side_is_flipped_to_us(self):
        # Pucar is the away side (A): his sets and stats must come out as "us".
        data = [row("Tomislav Pucar", group="pelapis")]
        [r] = build_records(card("ms_3249_lebrun_pucar.json"), EVENT, data)
        self.assertEqual(r["player"], "Tomislav Pucar")
        self.assertEqual(r["opponent"], "Felix Lebrun")
        self.assertEqual(r["oppInfo"], "FRA")
        self.assertEqual(r["result"], "L")
        self.assertEqual(r["score"], "0–3")
        self.assertEqual(r["sets"], [{"us": "9", "them": "11"}, {"us": "10", "them": "12"}, {"us": "5", "them": "11"}])
        self.assertEqual((r["ptsWon"], r["ptsOwnSrv"], r["ptsOppSrv"], r["bigLead"], r["consec"]),
                         ("24", "15", "9", "1", "6"))
        self.assertEqual((r["date"], r["year"], r["ev"], r["round"], r["type"], r["group"]),
                         ("2026-10-08", "2026", "China Smash", "Round of 16", "Singles", "pelapis"))

    def test_both_sides_tracked_gives_two_records(self):
        data = [row("Felix Lebrun"), row("Tomislav Pucar")]
        recs = build_records(card("ms_3249_lebrun_pucar.json"), EVENT, data)
        self.assertEqual([(r["player"], r["result"], r["ptsWon"]) for r in recs],
                         [("Felix Lebrun", "W", "34"), ("Tomislav Pucar", "L", "24")])

    def test_untracked_match_fails_loudly(self):
        with self.assertRaises(SystemExit):
            build_records(card("ms_3249_lebrun_pucar.json"), EVENT, [row("Wong Qi Shen")])

    def test_existing_spelling_reused(self):
        data = [row("Felix Lebrun", opponent="Pucar Tomislav")]
        [r] = build_records(card("ms_3249_lebrun_pucar.json"), EVENT, data)
        self.assertEqual(r["opponent"], "Pucar Tomislav")

    def test_duplicate_detected(self):
        data = [row("Felix Lebrun")]
        [r] = build_records(card("ms_3249_lebrun_pucar.json"), EVENT, data)
        self.assertTrue(is_duplicate(r, [r]))
        self.assertFalse(is_duplicate(r, data))


class DoublesTest(unittest.TestCase):
    def test_partner_and_opponents(self):
        data = [row("Cheng I-Ching")]
        [r] = build_records(card("xd_3249_final.json"), EVENT, data)
        self.assertEqual(r["type"], "Mixed Doubles")
        self.assertEqual(r["round"], "Final")
        self.assertEqual(r["player"], "Cheng I-Ching")
        self.assertEqual(r["partner"], "Lin Yun-Ju")
        self.assertEqual((r["opponent"], r["oppPartner"], r["oppInfo"]), ("Lim Jonghoon", "Shin Yubin", "KOR"))
        self.assertEqual(r["result"], "L")
        self.assertEqual(r["score"], "2–3")
        self.assertEqual(r["ptsWon"], "60")


if __name__ == "__main__":
    unittest.main()
