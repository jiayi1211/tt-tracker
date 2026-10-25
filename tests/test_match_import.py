"""Run: python3 -m unittest discover tests   (from the tt-tracker folder)

Fixtures are trimmed real responses:
  ms_3249_lebrun_pucar.json, xd_3249_final.json  WTT live match cards, China Smash 2026 (event 3249)
  wtt_archived_3308_choo.json                    WTT archive card (no live card, no stats), Youth Contender Bangkok 2026
  ittf_matches.json                              ITTF day-file matches: a team tie with stats (Asian Team Champs 2025),
                                                 a tie stuck as "live", a walkover, a mixed doubles (Asian/SEA Youth 2026)
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from match_import import (build_records, event_label, ittf_expand, plan,  # noqa: E402
                          wtt_match)

FIX = Path(__file__).parent / "fixtures"
EVENT = "China Smash 2026 Presented by Beijing Shijingshan Culture & Tourism Group"
STATS = ("ptsWon", "ptsOwnSrv", "ptsOppSrv", "bigLead", "consec")


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def row(player, **kw):
    return {"player": player, "partner": "", "opponent": "X Y", "oppPartner": "", "date": "2020-01-01",
            "group": "podium", "sets": [], **{f: "" for f in STATS}, **kw}


def wtt(name, data):
    return build_records(wtt_match(load(name), EVENT), data)


def ittf(key, data):
    f = load("ittf_matches.json")[key]
    return [r for m in ittf_expand(f["match"], f["date"], "Some Event") for r in build_records(m, data)]


class WttTest(unittest.TestCase):
    def test_away_side_is_flipped_to_us(self):
        # Pucar is the away side (A): his sets and stats must come out as "us".
        [r] = wtt("ms_3249_lebrun_pucar.json", [row("Tomislav Pucar", group="pelapis")])
        self.assertEqual((r["player"], r["opponent"], r["oppInfo"]), ("Tomislav Pucar", "Felix Lebrun", "FRA"))
        self.assertEqual((r["result"], r["score"]), ("L", "0–3"))
        self.assertEqual(r["sets"], [{"us": "9", "them": "11"}, {"us": "10", "them": "12"}, {"us": "5", "them": "11"}])
        self.assertEqual(tuple(r[f] for f in STATS), ("24", "15", "9", "1", "6"))
        self.assertEqual((r["date"], r["year"], r["ev"], r["round"], r["type"], r["group"]),
                         ("2026-10-08", "2026", "China Smash", "Round of 16", "Singles", "pelapis"))

    def test_both_sides_tracked_gives_two_records(self):
        recs = wtt("ms_3249_lebrun_pucar.json", [row("Felix Lebrun"), row("Tomislav Pucar")])
        self.assertEqual([(r["player"], r["result"], r["ptsWon"]) for r in recs],
                         [("Felix Lebrun", "W", "34"), ("Tomislav Pucar", "L", "24")])

    def test_untracked_match_gives_nothing(self):
        self.assertEqual(wtt("ms_3249_lebrun_pucar.json", [row("Wong Qi Shen")]), [])

    def test_existing_spelling_reused(self):
        [r] = wtt("ms_3249_lebrun_pucar.json", [row("Felix Lebrun", opponent="Pucar Tomislav")])
        self.assertEqual(r["opponent"], "Pucar Tomislav")

    def test_blank_stats_import_scores_only(self):
        # Some events (e.g. WTT Youth Contender Vientiane 2026) send the stat items with null values.
        card = load("ms_3249_lebrun_pucar.json")
        for res in card["full_msg"]["Results_all"][0]["MessagePayLoad"]["Competition"]["Result"]:
            for item in res["Competitor"][0]["StatsItems"]["StatsItem"]:
                item["Value"] = None
        [r] = build_records(wtt_match(card, EVENT), [row("Felix Lebrun")])
        self.assertEqual(tuple(r[f] for f in STATS), ("",) * 5)
        self.assertEqual((r["result"], r["score"]), ("W", "3–0"))

    def test_archived_card(self):
        # No live card: date comes from matchDateTime (MM/DD/YYYY), result from games, no stats.
        [r] = wtt("wtt_archived_3308_choo.json", [row("Choo Ke Ying")])
        self.assertEqual((r["date"], r["opponent"], r["result"], r["score"], r["round"]),
                         ("2026-05-21", "Thunyatorn Meekaew", "L", "2–3", "Round of 64"))
        self.assertEqual(tuple(r[f] for f in STATS), ("",) * 5)

    def test_doubles(self):
        [r] = wtt("xd_3249_final.json", [row("Cheng I-Ching")])
        self.assertEqual((r["type"], r["round"], r["player"], r["partner"]),
                         ("Mixed Doubles", "Final", "Cheng I-Ching", "Lin Yun-Ju"))
        self.assertEqual((r["opponent"], r["oppPartner"], r["oppInfo"]), ("Lim Jonghoon", "Shin Yubin", "KOR"))
        self.assertEqual((r["result"], r["score"], r["ptsWon"]), ("L", "2–3", "60"))


class IttfTest(unittest.TestCase):
    def test_team_rubbers_with_stats(self):
        data = [row("Karen Lyne"), row("Tee Ai Xin"), row("Alice Chang Li Sian")]
        recs = ittf("tie_with_stats", data)
        # 3 rubbers played, 2 left unplayed once the tie was decided -> 3 records.
        self.assertEqual([(r["player"], r["opponent"], r["score"]) for r in recs],
                         [("Karen Lyne", "Nabita Shrestha", "3–0"), ("Tee Ai Xin", "Bianca Rai", "3–0"),
                          ("Alice Chang Li Sian", "Evana Thapa", "3–0")])
        r = recs[0]
        self.assertEqual((r["round"], r["type"], r["result"], r["oppInfo"], r["date"]),
                         ("Team Match", "Singles", "W", "NEP", "2025-10-11"))
        self.assertEqual(tuple(r[f] for f in STATS), ("33", "14", "19", "5", "4"))

    def test_tie_stuck_live_still_imported(self):
        data = [row("Lee Hong An"), row("Im Jin Zhen"), row("Tey Hong Yu")]
        recs = ittf("tie_stuck_live", data)
        self.assertEqual([(r["player"], r["opponent"], r["result"]) for r in recs],
                         [("Lee Hong An", "Le Ellsworth", "L"), ("Im Jin Zhen", "Tan Nicholas", "L"),
                          ("Tey Hong Yu", "Yang Ze Yi", "W"), ("Lee Hong An", "Tan Nicholas", "L")])

    def test_walkover_is_unfinished(self):
        f = load("ittf_matches.json")["walkover"]
        [m] = ittf_expand(f["match"], f["date"], "Some Event")
        self.assertFalse(m["finished"])
        self.assertEqual(m["games"], [])

    def test_mixed_doubles_away_pair(self):
        [r] = ittf("mixed_doubles", [row("Tey Hong Yu"), row("Cheah Yu Zheng")])
        self.assertEqual((r["player"], r["partner"], r["type"], r["round"]),
                         ("Tey Hong Yu", "Cheah Yu Zheng", "Mixed Doubles", "Round of 16"))
        self.assertEqual((r["opponent"], r["oppPartner"], r["result"], r["score"]), ("Le Ellsworth", "Lai Chloe", "L", "0–3"))
        self.assertEqual(r["sets"], [{"us": "7", "them": "11"}, {"us": "11", "them": "13"}, {"us": "10", "them": "12"}])


class PlanTest(unittest.TestCase):
    def rec(self, **kw):
        base = row("Karen Lyne", opponent="Nabita Shrestha", date="2025-10-11",
                   sets=[{"us": "11", "them": "7"}, {"us": "11", "them": "6"}, {"us": "11", "them": "8"}])
        return {**base, **kw}

    def test_actions(self):
        new = self.rec(ptsWon="33", consec="4")
        self.assertEqual(plan([new], [])[0][0], "new")
        self.assertEqual(plan([new], [self.rec(ptsWon="30", consec="2")])[0][0], "have")
        action, _, _, fill = plan([new], [self.rec(consec="2")])[0]
        self.assertEqual((action, fill), ("fill stats", {"ptsWon": "33"}))  # only empty fields
        self.assertEqual(plan([new], [self.rec(sets=[{"us": "11", "them": "9"}])])[0][0], "differs")

    def test_hand_entered_date_off_by_a_day(self):
        self.assertEqual(plan([self.rec()], [self.rec(date="2025-10-12")])[0][0], "have")
        self.assertEqual(plan([self.rec()], [self.rec(date="2025-10-20")])[0][0], "new")

    def test_doubles_pair_in_either_order(self):
        new = self.rec(player="Lee Hong An", partner="Tey Hong Yu", opponent="A B", oppPartner="C D")
        old = self.rec(player="Tey Hong Yu", partner="Lee Hong An", opponent="C D", oppPartner="A B")
        self.assertEqual(plan([new], [old])[0][0], "have")


class LabelTest(unittest.TestCase):
    def test_event_label(self):
        self.assertEqual(event_label(EVENT), "China Smash")
        self.assertEqual(event_label("WTT Youth Contender Vientiane 2026 Presented by ICBC Bank (30 Jul 2026 - 02 Aug 2026)"),
                         "WTT Youth Contender Vientiane")
        self.assertEqual(event_label("ITTF-ATTU Asian Youth Championships Bangkok 2026"),
                         "ITTF-ATTU Asian Youth Championships Bangkok")


if __name__ == "__main__":
    unittest.main()
