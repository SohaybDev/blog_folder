from datetime import datetime, timezone

from src.db import backfill_missing_sport, save_raw_match
from src.fetcher import ScrapeBlocked
from src.items import InvalidMatch, validate_match
from src.sources import allt20_fixtures, cpl_matches, f1_races, pakistan_limited_overs, pakistan_tests
from src.trigger import trigger_content_generation

# add more scraper adapters here as they're built
SOURCES = [allt20_fixtures, cpl_matches, f1_races, pakistan_tests, pakistan_limited_overs]


def run() -> None:
    # Documents seeded before the pipeline was multi-sport have no `sport`, and
    # every sport-filtered query would silently miss them.
    backfilled = backfill_missing_sport("cricket")
    if backfilled:
        print(f"Backfilled sport=cricket on {backfilled} pre-existing documents.")

    saved = 0
    skipped = 0

    for source in SOURCES:
        print(f"Scraping via {source.__name__}...")
        try:
            matches = source.fetch_recent_matches()
        except ScrapeBlocked as err:
            print(f"  SKIPPED — {err}")
            continue
        except Exception as err:
            # One broken source must not stop the others from seeding.
            print(f"  FAILED — {type(err).__name__}: {err}")
            continue

        for match in matches:
            try:
                validate_match(match)
            except InvalidMatch as err:
                print(f"  skipped {match.get('matchTitle', '?')}: {err}")
                skipped += 1
                continue

            match["scrapedAt"] = datetime.now(timezone.utc)
            match_id = save_raw_match(match)
            detail = _describe(match)
            print(f"  saved {match['matchTitle']} -> {match_id} ({detail})")
            saved += 1

    print(f"Done. {saved} saved, {skipped} skipped.")

    if saved > 0:
        trigger_content_generation()


def _describe(match: dict) -> str:
    """Short per-record summary — mainly so a run makes it obvious whether
    results were captured or only a fixture."""
    scorecard = match.get("scorecard") or {}
    standings = len(match.get("standings", []))
    performances = len(match.get("playerPerformances", []))
    if standings:
        return f"{standings} classified finishers"
    if performances:
        return f"{performances} player performances"
    if scorecard.get("result"):
        return f"result: {scorecard['result']}"
    return f"fixture only, {len(match.get('sourceImages', []))} image urls"


if __name__ == "__main__":
    run()
