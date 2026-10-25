"""Import every tracked player's results since Jan 2025 from WTT and ITTF.

    python3 sync_results.py            # dry run: list what would change
    python3 sync_results.py --write    # apply to data.json (what the tracker's Update button runs)
    python3 sync_results.py --full     # also re-scan events already scanned (e.g. after adding a player)

Same rules as match_import.py: tracked players are everyone in data.json as `player`; new matches
are added, missing Match Stats filled, nothing else in existing records is touched.

Finished events are remembered in sync_state.json and skipped next time, so only the first
run (and --full) has to read every event; results of a finished event don't change.
"""
import argparse
import json
import re
import sys
import urllib.error
from datetime import date, datetime
from pathlib import Path

import match_import as mi

SINCE = date(2025, 1, 1)
STATE_FILE = Path(__file__).with_name("sync_state.json")
WTT_EVENTS = "https://wtt-website-api-vm-frontdoor-hhaec5epbhdyfugz.a01.azurefd.net/primary/api/cms/GetAllEventsOnlyWithEventName"


def ittf_events(today):
    """(key, name, fetch) for ITTF events that started between SINCE and today."""
    for year in range(SINCE.year, today.year + 1):
        for c in mi.ittf_get(f"champs_d{year}.json"):
            start = datetime.strptime(c["sort"], "%Y%m%d").date()
            if SINCE <= start <= today:
                yield f"ittf:{c['Champ']}", c["Desc"], lambda champ=c["Champ"]: ittf_fetch(champ)


def ittf_fetch(champ):
    info = mi.ittf_get(f"{champ}/champ.json")
    return mi.ittf_event_matches(champ, info), bool(info.get("isFinished"))


def wtt_events(today, roster):
    """(key, name, fetch) for WTT events that ended between SINCE and yesterday."""
    for e in mi.wtt_get(WTT_EVENTS):
        # "WTT Youth Contender Bangkok 2026 (18 May 2026 - 21 May 2026)"
        m = re.search(r"\((\d{1,2} \w{3} \d{4}) - (\d{1,2} \w{3} \d{4})\)", e["eventName"])
        if not m:
            continue
        end = datetime.strptime(m.group(2), "%d %b %Y").date()
        if SINCE <= end < today:
            yield (f"wtt:{e['eventId']}", e["eventName"],
                   # Only finished events have an archive, so anything fetched here is finished.
                   lambda eid=e["eventId"], name=e["eventName"]: (mi.wtt_event_matches(eid, name, roster), True))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="apply the changes to data.json")
    ap.add_argument("--full", action="store_true", help="ignore sync_state.json and re-scan every event")
    args = ap.parse_args()

    data = json.loads(mi.DATA_FILE.read_text(encoding="utf-8"))
    roster = mi.tracked_players(data)
    done = set() if args.full or not STATE_FILE.exists() else set(json.loads(STATE_FILE.read_text())["done"])
    today = date.today()

    totals, unrecognised, failed = {}, {}, []
    events = list(ittf_events(today)) + list(wtt_events(today, roster))
    todo = [e for e in events if e[0] not in done]
    print(f"{len(events)} events since {SINCE}; {len(events) - len(todo)} already scanned, {len(todo)} to scan.")
    for key, name, fetch in todo:
        try:
            matches, finished = fetch()
            actions = mi.actions_for(matches, data)
        except (LookupError, ValueError, KeyError, urllib.error.URLError) as e:
            # ValueError also covers WTT redirecting an unarchived event to its HTML home page.
            failed.append(f"{name}: {type(e).__name__}: {e}")
            continue
        for k, n in mi.untracked(matches, data).items():
            unrecognised[k] = unrecognised.get(k, 0) + n
        changes = [a for a in actions if a[0] != "have"]
        if changes:
            print(f"\n== {name}")
            mi.print_actions(changes)
        for a, n in mi.count(actions).items():
            totals[a] = totals.get(a, 0) + n
        data = mi.apply(actions, data)  # later events must see this event's additions
        if finished:
            done.add(key)

    print("\nTotal: " + (", ".join(f"{n} {a}" for a, n in sorted(totals.items())) or "no tracked matches"))
    for (name, org), n in sorted(unrecognised.items()):
        print(f"Not recognised: {name} ({org}), {n} match(es). Add to ALIASES in match_import.py if tracked.")
    for f in failed:
        print(f"Could not read {f}")

    if args.write:
        mi.save(data)
        STATE_FILE.write_text(json.dumps({"done": sorted(done)}, indent=1) + "\n")
        print(f"Wrote {mi.DATA_FILE.name}: {totals.get('new', 0)} added, {totals.get('fill stats', 0)} given stats.")
    else:
        print("Dry run: nothing written. Add --write to apply.")


if __name__ == "__main__":
    main()
