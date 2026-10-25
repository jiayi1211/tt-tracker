"""Import match results from WTT or ITTF into data.json.

    python3 match_import.py "<URL>"           # dry run: show what would change
    python3 match_import.py "<URL>" --write   # apply it to data.json

URLs:
  WTT   https://www.worldtabletennis.com/PostMatchCenter?eventId=...&docCode=...
        -> that one match
  ITTF  https://results.ittf.com/ittf-web-results/html/TTE3472/results.html#/...
        -> the whole event (any page of it), team rubbers included

Tracked players are everyone already in data.json as `player`. Each of their matches is:
  new         added; Match Stats filled when the source has them, else left blank (dashes)
  fill stats  already in data.json without stats and the source has them -> only the
              empty stat fields are filled, nothing else is touched
  have        already in data.json, nothing to add
  differs     looks like a record in data.json but the set scores disagree -> reported only

Sources are the websites' own undocumented JSON. WTT only answers with a
worldtabletennis.com Referer, which is why the tracker page can't call it itself.
Either may change without notice.
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

DATA_FILE = Path(__file__).with_name("data.json")
WTT_API = "https://wtt-website-api-vm-frontdoor-hhaec5epbhdyfugz.a01.azurefd.net/liveeventsapi/api/cms/"
WTT_ARCHIVE = "https://wtt-web-frontdoor-cthahjeqhbh6aqe3.a01.azurefd.net/websitearchivedresults/"
ITTF = "https://results.ittf.com/ittf-web-results/html/"

STAT_FIELDS = ["ptsWon", "ptsOwnSrv", "ptsOppSrv", "bigLead", "consec"]
WTT_STATS = {"PTS_WON": "ptsWon", "PTS_SERVICE_WON": "ptsOwnSrv", "PTS_OPP_SERVICE_WON": "ptsOppSrv",
             "LEAD_MAX": "bigLead", "PTS_CONSEC": "consec"}
ITTF_STATS = {"ST_PW": "ptsWon", "ST_PWOS": "ptsOwnSrv", "ST_PWOPS": "ptsOppSrv",
              "ST_BL": "bigLead", "ST_MCPW": "consec"}
ROUNDS = {
    "final": "Final", "finals": "Final",
    "semifinal": "Semifinal", "semifinals": "Semifinal",
    "quarterfinal": "Quarterfinal", "quarterfinals": "Quarterfinal",
    "qualification": "Qualifying", "qualifying": "Qualifying",
}
# Orgs whose names the tracker writes family-name first ("Kim Minhyeok", "Liu Zitong").
# Heuristic for names not already in data.json; fix odd ones in the tracker's edit form.
FAMILY_FIRST = {"CHN", "KOR", "PRK", "TPE", "HKG", "MAC", "VIE", "SGP", "MAS"}

# Source names data.json spells too differently to match automatically -> the tracker's name.
ALIASES = {
    "MUHAMMAD Faiz": "Muhd Faiz Danial Bin Mohd Hizir",  # ITTF
}

# A source match, before it becomes tracker records:
#   {"date": "2026-06-29", "ev": str, "round": str, "type": str, "games": [(home, away), ...],
#    "finished": bool, "sides": [{"players": [(name, org)], "org": str, "won": bool,
#                                 "stats": {field: value}}] * 2}


# ── WTT ──────────────────────────────────────────────────────────────────────

def wtt_get(url):
    req = urllib.request.Request(url, headers={
        "Referer": "https://www.worldtabletennis.com/",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        body, encoding = r.read(), r.headers.get("Content-Encoding")
    if not body:  # 204: WTT has nothing for this
        raise LookupError(f"WTT returned no data for {url}")
    if encoding == "br":  # archive files are stored brotli-compressed whatever we ask for
        try:
            import brotli
        except ImportError:
            sys.exit("Reading WTT's archive needs the brotli package: pip install brotli")
        body = brotli.decompress(body)
    return json.loads(body)


def wtt_matches(url):
    q = parse_qs(urlparse(url).query)
    try:
        event_id, doc_code = q["eventId"][0], q["docCode"][0]
    except KeyError:
        sys.exit("WTT URL needs eventId and docCode, e.g. a PostMatchCenter link.")
    if "TEAM" in doc_code:
        sys.exit("WTT team matches aren't supported yet. Open the individual rubber's link instead.")
    event_name = wtt_get(f"{WTT_API}GetEventName/{event_id}")[0]["eventName"]
    try:
        card = wtt_live_card(event_id, doc_code)
    except LookupError:
        # Many youth and older matches have no live card; the event archive still has the result.
        card = next((c for c in wtt_archive(event_id) if c["documentCode"] == doc_code), None)
        if card is None:
            sys.exit(f"WTT has no result for {doc_code} at event {event_id}.")
    if card.get("resultStatus") != "OFFICIAL":
        sys.exit(f"Match isn't official yet (status: {card.get('resultStatus')}).")
    return [wtt_match(card, event_name)]


def wtt_live_card(event_id, doc_code):
    """Full match card, with Match Stats when WTT has them. LookupError when there is none."""
    return wtt_get(f"{WTT_API}GetMatchCardDetails/{event_id}/{quote(doc_code)}"
                   "?no_cache=true&require_full_msg=true&require_play_by_play_full=false")


def wtt_archive(event_id):
    """Every official match card of a finished event: scores, no stats."""
    rows = wtt_get(f"{WTT_ARCHIVE}{event_id}/officialresult/officialresult.json")
    return [r["match_card"] for r in rows if r.get("match_card") and r["match_card"].get("resultStatus") == "OFFICIAL"]


def wtt_event_matches(event_id, event_name, roster):
    """Source matches at a finished WTT event involving anyone in `roster` (team ties skipped)."""
    out = []
    for card in wtt_archive(event_id):
        if "TEAM" in card["documentCode"] or not any(
                find_known(p["playerName"], roster) for c in card["competitiors"] for p in c["players"]):
            continue
        time.sleep(0.2)  # be gentle with WTT's API
        try:
            live = wtt_live_card(event_id, card["documentCode"])
        except LookupError:
            live = None
        out.append(wtt_match(live if live and live.get("resultStatus") == "OFFICIAL" else card, event_name))
    return out


def official_payload(card):
    msg = next((m for m in (card.get("full_msg") or {}).get("Results_all", [])
                if m["MessageType"] == "DT_RESULT_OFFICIAL"), None)
    if msg is None:
        return None
    p = msg["MessagePayLoad"]
    return json.loads(p) if isinstance(p, str) else p


def wtt_match(card, event_name):
    games = [tuple(int(x) for x in g.split("-")) for g in card["resultsGameScores"].split(",") if g != "0-0"]
    wlt, stats = {}, {}
    payload = official_payload(card)
    if payload:
        for res in payload["Competition"]["Result"]:
            comp = res["Competitor"]
            comp = comp[0] if isinstance(comp, list) else comp  # WTT sends both shapes
            wlt[comp["Code"]] = res["Wlt"]
            # Some events send the stat items with null values: treat those as no stats.
            stats[comp["Code"]] = {WTT_STATS[i["Code"]]: i["Value"] for i in comp["StatsItems"]["StatsItem"]
                                   if i["Pos"] == "TOT" and i["Code"] in WTT_STATS and i["Value"] not in (None, "")}
        day = payload["Competition"]["ExtendedInfos"]["UnitDateTime"]["StartDateLocal"][:10]
    else:  # archived card: no result message, so no stats; date is MM/DD/YYYY
        m, d, y = card["matchDateTime"]["startDateLocal"][:10].split("/")
        day = f"{y}-{m}-{d}"
    sides = []
    for i, c in enumerate(card["competitiors"]):
        won_games = sum(g[i] > g[1 - i] for g in games)
        cid = c["competitiorId"]
        sides.append({
            "players": [(p["playerName"], p["playerOrgCode"]) for p in c["players"]],
            "org": c["competitiorOrg"],
            "won": wlt[cid] == "W" if cid in wlt else won_games > len(games) - won_games,
            "stats": stats.get(cid, {}),
        })
    return {"date": day, "ev": event_label(event_name), "round": round_name(card["subEventDescription"]),
            "type": match_type(card["subEventName"]), "games": games, "sides": sides, "finished": True}


# ── ITTF ─────────────────────────────────────────────────────────────────────

def ittf_get(path):
    with urllib.request.urlopen(urllib.request.Request(ITTF + path), timeout=60) as r:
        return json.load(r)


def ittf_matches(url):
    m = re.search(r"/html/(TT\w+)/", url)
    if not m:
        sys.exit("ITTF URL should look like https://results.ittf.com/ittf-web-results/html/TTE3472/results.html")
    return ittf_event_matches(m.group(1), ittf_get(f"{m.group(1)}/champ.json"))


def ittf_event_matches(champ, info):
    """Every source match at an ITTF event; `info` is its champ.json."""
    ev = event_label(info["champDesc"])
    return [s for d in info["dates"] for match in ittf_get(f"{champ}/match/d{d['raw']}.json")
            for s in ittf_expand(match, d["raw"], ev)]


def ittf_expand(match, day, ev):
    """Source matches for one ITTF match: itself, or a team tie's played rubbers.

    Not filtered on Status: some finished ties stay "live" (2) for good, so each match is
    judged by its own games instead (see ittf_match).
    Matches in formats the tracker doesn't record are skipped: Para team ties (no "Home") and
    classes that aren't singles/doubles (e.g. the Parkinson's championship's "Alzheimer" class).
    """
    if "Home" not in match:
        return []
    try:
        return _ittf_expand(match, day, ev)
    except (ValueError, KeyError):  # not singles/doubles (match_type), or a rubber without "Home"
        return []


def _ittf_expand(match, day, ev):
    if not match["IsTeam"]:
        subs = [ittf_match(match, day, ev, round_name(match["Desc"]), match_type(match["Desc"]))]
    else:
        subs = [ittf_match(sub, day, ev, "Team Match",
                           "Singles" if len(sub["Home"].get("Members") or [sub["Home"]]) == 1
                           else match_type(match["Desc"]))
                for sub in match.get("SubMatches", [])]
    return [s for s in subs if s]


def ittf_match(m, day, ev, round_, type_):
    """Source match, or None for a rubber left unplayed once its tie was decided."""
    home, away = m["Home"], m["Away"]
    # Walkovers show "WO" with 0-0 games; drop those games so the match counts as unfinished.
    games = [(int(h["Res"]), int(a["Res"])) for h, a in zip(home["Splits"], away["Splits"])
             if h["Res"] != "" and a["Res"] != "" and (h["Res"], a["Res"]) != ("0", "0")]
    if not games and "WO" not in (home["Res"], away["Res"]):
        return None
    wins = [sum(g[0] > g[1] for g in games), sum(g[1] > g[0] for g in games)]
    sides = []
    for i, side in enumerate((home, away)):
        st = side.get("Stats") or {}
        sides.append({
            "players": [(p["Desc"], p.get("Org", side["Org"])) for p in side.get("Members") or [side]],
            "org": side["Org"],
            "won": wins[i] > wins[1 - i],  # `Win` is unreliable on team rubbers
            "stats": {f: st[c] for c, f in ITTF_STATS.items() if st.get(c) not in (None, "", "-")},
        })
    return {"date": day, "ev": ev, "round": round_, "type": type_, "games": games, "sides": sides,
            "finished": max(wins) >= len(home["Splits"]) // 2 + 1}


# ── Names, labels ────────────────────────────────────────────────────────────

def tokens(name):
    return set(re.findall(r"[a-z0-9]+", name.lower().replace("-", "")))


def same_person(a, b):
    """Token-set match, allowing one name to have extra parts (e.g. 'Bt', 'Bin')."""
    ta, tb = tokens(a), tokens(b)
    return len(ta & tb) >= 2 and (ta <= tb or tb <= ta)


def find_known(name, known):
    if ALIASES.get(name) in known:
        return ALIASES[name]
    hits = sorted(k for k in known if same_person(name, k))
    # data.json can spell one person both ways round ("Chua Josh", "Josh Chua"): same person.
    if len({frozenset(tokens(h)) for h in hits}) > 1:
        raise ValueError(f"'{name}' matches several names in data.json: {hits}")
    return hits[0] if hits else None


def tracker_roster():
    """{player: group} from the tracker's PLAYER_GROUP, so rostered players count as tracked
    before their first match is in data.json."""
    html = DATA_FILE.with_name("tt_results_tracker.html").read_text(encoding="utf-8")
    block = re.search(r"const PLAYER_GROUP = \{(.*?)\};", html, re.S)
    if not block:
        raise ValueError("PLAYER_GROUP not found in tt_results_tracker.html")
    return dict(re.findall(r"'([^']+)'\s*:\s*'([^']+)'", block.group(1)))


def tracked_players(data):
    return {m["player"] for m in data} | set(tracker_roster())


def tracker_name(source_name, org, known):
    """'LEBRUN Felix' -> existing spelling in data.json, else 'Felix Lebrun' / 'Kim Minhyeok'."""
    existing = find_known(source_name, known)
    if existing:
        return existing
    parts = source_name.split()
    family = [p.title() for p in parts if p.isupper()]
    given = [p for p in parts if not p.isupper()]
    return " ".join(family + given if org in FAMILY_FIRST else given + family)


def match_type(sub_event):
    s = sub_event.lower()
    if "singles" in s:
        return "Singles"
    if "mixed" in s:
        return "Mixed Doubles"
    if "women" in s or "girls" in s:
        return "Women's Doubles"
    if "men" in s or "boys" in s:
        return "Men's Doubles"
    raise ValueError(f"Unrecognised event type: {sub_event}")


def round_name(description):
    # "Men's Singles - Round of 16 - Match 1" -> "Round of 16"; "... - Group 17 - ..." -> "Group Stage"
    parts = description.split(" - ")
    raw = parts[1] if len(parts) > 1 else description
    if raw.lower().startswith("group"):
        return "Group Stage"
    return ROUNDS.get(re.sub(r"[^a-z]", "", raw.lower()), raw)


def event_label(event_name):
    """'China Smash 2026 Presented by ... (dates)' -> 'China Smash', like the tracker's names."""
    name = re.sub(r"\s*\(.*\)\s*$", "", event_name)
    name = re.sub(r"\s+Presented by.*$", "", name, flags=re.I)
    return re.sub(r"\s+20\d\d$", "", name).strip()


# ── Records ──────────────────────────────────────────────────────────────────

def build_records(match, data):
    """Tracker records for each side of `match` that has a tracked player (none -> [])."""
    players_known = tracked_players(data)
    names_known = players_known | {m[k] for m in data for k in ("opponent", "partner", "oppPartner") if m[k]}
    groups = {**{m["player"]: m["group"] for m in data if m.get("group")}, **tracker_roster()}

    records = []
    for i, side in enumerate(match["sides"]):
        ours = [find_known(name, players_known) for name, _ in side["players"]]
        if not any(ours):
            continue
        other = match["sides"][1 - i]
        names = [tracker_name(name, org, names_known) for name, org in side["players"]]
        player = next(o for o in ours if o)
        partner = next((n for n in names if not same_person(n, player)), "")
        opp = [tracker_name(name, org, names_known) for name, org in other["players"]]
        sets = [{"us": str(g[i]), "them": str(g[1 - i])} for g in match["games"]]
        won = sum(int(s["us"]) > int(s["them"]) for s in sets)
        records.append({
            "id": None,  # assigned when written (assign_ids)
            "date": match["date"], "year": match["date"][:4], "ev": match["ev"],
            "player": player, "partner": partner,
            "opponent": opp[0], "oppPartner": opp[1] if len(opp) > 1 else "",
            "round": match["round"], "type": match["type"],
            "result": "W" if side["won"] else "L",
            "score": f"{won}–{len(sets) - won}",
            "sets": sets, "oppInfo": other["org"], "notes": "",
            "group": groups.get(player, "podium"),
            **{f: side["stats"].get(f, "") for f in STAT_FIELDS},
        })
    return records


def sets_of(rec):
    try:
        return [(int(s["us"]), int(s["them"])) for s in rec["sets"] if s]
    except (TypeError, ValueError, KeyError):
        return None


def same_pair(a, b):
    """Same players on both sides; a doubles pair may be stored in either order."""
    def side(r, *keys):
        return [r[k] for k in keys if r[k]]
    ours_a, ours_b = side(a, "player", "partner"), side(b, "player", "partner")
    opp_a, opp_b = side(a, "opponent", "oppPartner"), side(b, "opponent", "oppPartner")
    return (sorted(ours_a) == sorted(ours_b) and len(opp_a) == len(opp_b)
            and all(any(same_person(x, y) for y in opp_b) for x in opp_a))


def plan(records, data):
    """[(action, new record, existing record or None, stat fields to fill)] — see module docstring."""
    out = []
    for rec in records:
        d = date.fromisoformat(rec["date"])
        same = [m for m in data if same_pair(m, rec)]
        # Same match = same day, or within two days with identical sets (hand-entered dates can be off).
        found = [m for m in same if m["date"] == rec["date"]] or \
                [m for m in same if abs((date.fromisoformat(m["date"]) - d).days) <= 2
                 and sets_of(m) == sets_of(rec)]
        if not found:
            out.append(("new", rec, None, {}))
            continue
        same_sets = [m for m in found if sets_of(m) == sets_of(rec)]
        if len(same_sets) != 1:
            out.append(("differs", rec, found[0], {}))
            continue
        old = same_sets[0]
        fill = {f: rec[f] for f in STAT_FIELDS if rec[f] and not str(old.get(f, "")).strip()}
        out.append(("fill stats" if fill else "have", rec, old, fill))
    return out


def assign_ids(new, data):
    """Unique ids like the tracker's Date.now(), never colliding with existing ones."""
    start = max([int(time.time() * 1000)] + [m["id"] + 1 for m in data])
    for k, rec in enumerate(new):
        rec["id"] = start + k
    return new


def untracked(matches, data):
    """{(source name, org): matches} for players from a tracked player's country who matched nobody."""
    known = tracked_players(data)
    orgs = {org for m in matches for side in m["sides"] for name, org in side["players"] if find_known(name, known)}
    counts = {}
    for m in matches:
        for side in m["sides"]:
            for name, org in side["players"]:
                if org in orgs and not find_known(name, known):
                    counts[(name, org)] = counts.get((name, org), 0) + 1
    return counts


def actions_for(matches, data):
    """plan() for a set of source matches, plus unfinished ones (reported, never written)."""
    records, unfinished = [], []
    for m in matches:
        (records if m["finished"] else unfinished).extend(build_records(m, data))
    actions = plan(records, data) + [("not finished", r, None, {}) for r in unfinished]
    # Keep the event name data.json already uses for this event (e.g. records typed in by hand).
    used = {old["ev"] for _, _, old, _ in actions if old}
    if len(used) == 1:
        for _, rec, _, _ in actions:
            rec["ev"] = next(iter(used))
    return actions


def print_actions(actions, show_have=True):
    for action, rec, old, fill in sorted(actions, key=lambda a: (a[0], a[1]["date"], a[1]["player"])):
        if action == "have" and not show_have:
            continue
        who = rec["player"] + (f" / {rec['partner']}" if rec["partner"] else "")
        opp = rec["opponent"] + (f" / {rec['oppPartner']}" if rec["oppPartner"] else "")
        stats = "stats" if rec["ptsWon"] else "no stats"
        line = f"[{action}] {rec['date']} {rec['round']} | {who} vs {opp} | {rec['score']} {sets_of(rec)} | {stats}"
        if action == "differs":
            line += f"\n    data.json has {old['date']} sets {sets_of(old)}"
        if fill:
            line += f"\n    fill {fill}"
        print(line)


def count(actions):
    counts = {}
    for a, *_ in actions:
        counts[a] = counts.get(a, 0) + 1
    return counts


def apply(actions, data):
    """Fill stats in place and return data with the new records in front (newest first)."""
    for _, _, old, fill in actions:
        if old:
            old.update(fill)
    return assign_ids([rec for a, rec, _, _ in actions if a == "new"], data) + data


def save(data):
    # Same layout as the tracker's githubSave (JSON.stringify(data, null, 2)).
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="WTT PostMatchCenter URL or any ITTF results URL")
    ap.add_argument("--write", action="store_true", help="apply the changes to data.json")
    args = ap.parse_args()

    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    host = urlparse(args.url).netloc
    if host.endswith("worldtabletennis.com"):
        fetch = wtt_matches
    elif host == "results.ittf.com":
        fetch = ittf_matches
    else:
        sys.exit("Give a worldtabletennis.com PostMatchCenter URL or a results.ittf.com URL.")
    try:
        matches = fetch(args.url)
        actions = actions_for(matches, data)
    except ValueError as e:
        sys.exit(str(e))
    if not actions:
        sys.exit("No matches with a player already in data.json.")
    print_actions(actions)
    print(f"\n{actions[0][1]['ev']}: " + ", ".join(f"{n} {a}" for a, n in sorted(count(actions).items())))
    for (name, org), n in sorted(untracked(matches, data).items()):
        print(f"Not recognised: {name} ({org}), {n} match(es). If this is a tracked player, add them to ALIASES.")

    if args.write:
        save(apply(actions, data))
        c = count(actions)
        print(f"Wrote {DATA_FILE.name}: {c.get('new', 0)} added, {c.get('fill stats', 0)} given stats.")
    else:
        print("Dry run: nothing written. Add --write to apply.")


if __name__ == "__main__":
    main()
