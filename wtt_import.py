"""Import one WTT match (Post Match Center URL) as tracker records.

    python3 wtt_import.py "<PostMatchCenter URL>"           # print records
    python3 wtt_import.py "<PostMatchCenter URL>" --write   # also prepend to data.json

A record is built for each side that has a tracked player (anyone who already
appears as `player` in data.json), matching what the tracker's form produces.

Source: WTT's undocumented website API (the calls the Post Match Center page
makes). It only answers when the Referer is worldtabletennis.com, so it can't be
called from the tracker page itself. It may change without notice.
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

API = "https://wtt-website-api-vm-frontdoor-hhaec5epbhdyfugz.a01.azurefd.net/liveeventsapi/api/cms/"
DATA_FILE = Path(__file__).with_name("data.json")

# WTT stat code -> tracker field
STATS = {
    "PTS_WON": "ptsWon",
    "PTS_SERVICE_WON": "ptsOwnSrv",
    "PTS_OPP_SERVICE_WON": "ptsOppSrv",
    "LEAD_MAX": "bigLead",
    "PTS_CONSEC": "consec",
}
ROUNDS = {
    "final": "Final", "finals": "Final",
    "semifinal": "Semifinal", "semifinals": "Semifinal",
    "quarterfinal": "Quarterfinal", "quarterfinals": "Quarterfinal",
    "qualification": "Qualifying", "qualifying": "Qualifying",
    "group": "Group Stage", "groups": "Group Stage", "groupstage": "Group Stage",
}
# Orgs whose names the tracker writes family-name first ("Kim Minhyeok", "Liu Zitong").
# Heuristic for names not already in data.json; fix odd ones in the tracker's edit form.
FAMILY_FIRST = {"CHN", "KOR", "PRK", "TPE", "HKG", "MAC", "VIE", "SGP", "MAS"}


def get_json(path):
    req = urllib.request.Request(API + path, headers={
        "Referer": "https://www.worldtabletennis.com/",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
    if not body:  # 204: WTT has nothing for this (e.g. older events' match cards)
        raise LookupError(f"WTT returned no data for {path}")
    return json.loads(body)


def fetch_match(url):
    q = parse_qs(urlparse(url).query)
    try:
        event_id, doc_code = q["eventId"][0], q["docCode"][0]
    except KeyError:
        sys.exit("URL needs eventId and docCode, e.g. a PostMatchCenter link.")
    if "TEAM" in doc_code:
        sys.exit("Team matches aren't supported yet. Open the individual rubber's link instead.")
    card = get_json(f"GetMatchCardDetails/{event_id}/{quote(doc_code)}"
                    "?no_cache=true&require_full_msg=true&require_play_by_play_full=false")
    if card.get("resultStatus") != "OFFICIAL":
        sys.exit(f"Match isn't official yet (status: {card.get('resultStatus')}).")
    event_name = get_json(f"GetEventName/{event_id}")[0]["eventName"]
    return card, event_name



def tokens(name):
    return set(re.findall(r"[a-z0-9]+", name.lower().replace("-", "")))


def same_person(a, b):
    """Token-set match, allowing one name to have extra parts (e.g. 'Bt', 'Bin')."""
    ta, tb = tokens(a), tokens(b)
    return len(ta & tb) >= 2 and (ta <= tb or tb <= ta)


def find_known(name, known):
    hits = [k for k in known if same_person(name, k)]
    if len(hits) > 1:
        sys.exit(f"'{name}' matches several names in data.json: {hits}")
    return hits[0] if hits else None


def tracker_name(wtt_name, org, known):
    """'LEBRUN Felix' -> existing spelling in data.json, else 'Felix Lebrun' / 'Kim Minhyeok'."""
    existing = find_known(wtt_name, known)
    if existing:
        return existing
    parts = wtt_name.split()
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
    sys.exit(f"Unrecognised event type: {sub_event}")


def round_name(description):
    # "Men's Singles - Round of 16 - Match 1" -> "Round of 16"
    parts = description.split(" - ")
    raw = parts[1] if len(parts) > 1 else description
    return ROUNDS.get(re.sub(r"[^a-z]", "", raw.lower()), raw)


def event_label(event_name):
    """'China Smash 2026 Presented by ... (dates)' -> 'China Smash', like the tracker's names."""
    name = re.sub(r"\s*\(.*\)\s*$", "", event_name)
    name = re.sub(r"\s+Presented by.*$", "", name, flags=re.I)
    return re.sub(r"\s+20\d\d$", "", name).strip()


def official_payload(card):
    msg = next(m for m in card["full_msg"]["Results_all"] if m["MessageType"] == "DT_RESULT_OFFICIAL")
    p = msg["MessagePayLoad"]
    return json.loads(p) if isinstance(p, str) else p


def side_stats(card):
    """Competitor id -> (W/L, {tracker stat field: value}) from the official result."""
    out = {}
    for res in official_payload(card)["Competition"]["Result"]:
        comp = res["Competitor"]
        comp = comp[0] if isinstance(comp, list) else comp  # WTT sends both shapes
        items = comp["StatsItems"]["StatsItem"]
        stats = {STATS[i["Code"]]: i["Value"] for i in items
                 if i["Pos"] == "TOT" and i["Code"] in STATS and i["Value"] not in (None, "")}
        missing = set(STATS.values()) - set(stats)
        if missing:
            raise ValueError(f"WTT has no {sorted(missing)} for competitor {comp['Code']}; not using partial stats.")
        out[comp["Code"]] = (res["Wlt"], stats)
    return out


def build_records(card, event_name, data):
    players_known = {m["player"] for m in data}
    names_known = players_known | {m[k] for m in data for k in ("opponent", "partner", "oppPartner") if m[k]}
    groups = {m["player"]: m["group"] for m in data if m.get("group")}

    by_side = side_stats(card)
    payload = official_payload(card)
    date = payload["Competition"]["ExtendedInfos"]["UnitDateTime"]["StartDateLocal"][:10]

    sides = card["competitiors"]
    games = [g.split("-") for g in card["resultsGameScores"].split(",") if g != "0-0"]

    records = []
    for i, side in enumerate(sides):
        ours = [find_known(p["playerName"], players_known) for p in side["players"]]
        if not any(ours):
            continue
        other = sides[1 - i]
        names = [tracker_name(p["playerName"], p["playerOrgCode"], names_known) for p in side["players"]]
        player = next(o for o in ours if o)
        partner = next((n for n in names if not same_person(n, player)), "")
        opp = [tracker_name(p["playerName"], p["playerOrgCode"], names_known) for p in other["players"]]
        sets = [{"us": g[i], "them": g[1 - i]} for g in games]
        won = sum(int(s["us"]) > int(s["them"]) for s in sets)
        wlt, stats = by_side[side["competitiorId"]]
        records.append({
            "id": int(time.time() * 1000) + i,
            "date": date, "year": date[:4], "ev": event_label(event_name),
            "player": player, "partner": partner,
            "opponent": opp[0], "oppPartner": opp[1] if len(opp) > 1 else "",
            "round": round_name(card["subEventDescription"]),
            "type": match_type(card["subEventName"]),
            "result": wlt,
            "score": f"{won}–{len(sets) - won}",
            "sets": sets, "oppInfo": other["competitiorOrg"], "notes": "",
            "group": groups.get(player, "podium"),
            **stats,
        })
    if not records:
        sys.exit("Neither side has a player already in data.json: "
                 + " vs ".join(s["competitiorName"] for s in sides))
    return records


def is_duplicate(rec, data):
    return any(m["date"] == rec["date"] and m["player"] == rec["player"]
               and same_person(m["opponent"], rec["opponent"]) for m in data)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="worldtabletennis.com PostMatchCenter URL")
    ap.add_argument("--write", action="store_true", help="prepend the records to data.json")
    args = ap.parse_args()

    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    try:
        card, event_name = fetch_match(args.url)
        records = build_records(card, event_name, data)
    except (ValueError, LookupError) as e:
        sys.exit(str(e))
    print(json.dumps(records, indent=2, ensure_ascii=False))

    if args.write:
        new = []
        for r in records:
            if is_duplicate(r, data):
                print(f"Skipped (already in data.json): {r['date']} {r['player']} vs {r['opponent']}", file=sys.stderr)
            else:
                new.append(r)
        if new:
            # Same layout as the tracker's githubSave (JSON.stringify(data, null, 2)), newest first.
            DATA_FILE.write_text(json.dumps(new + data, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"Added {len(new)} record(s) to {DATA_FILE.name}", file=sys.stderr)


if __name__ == "__main__":
    main()
