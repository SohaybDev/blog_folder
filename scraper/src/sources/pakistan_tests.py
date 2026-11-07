"""Pakistan's current Test tours, from the Wikipedia tour article.

Test cricket has no keyless factual feed the way the shorter formats do.
TheSportsDB — the source behind `cpl_matches` — carries franchise T20 leagues
(PSL, CPL, BBL, IPL) and county cricket, but no international Test series at
all: `search_all_leagues.php?s=Cricket` returns no Test competition for any
country. ESPN Cricinfo 403s automated requests behind its CDN and Cricbuzz sets
`Disallow: /` for generic bots, both re-checked while this was written.

Wikipedia's per-tour articles do carry the full Test record in a fixed
template, and `/wiki/<Article>` is explicitly allowed for the wildcard user
agent (`/w/api.php` and `/api/` are *not*, so this reads the rendered article
rather than the MediaWiki API — `fetcher.get` enforces that either way). Text
is CC BY-SA 4.0, credited per article through `scorecard.attribution`.

One Test becomes one `raw_matches` document. Cricket is head-to-head, so
`teams` holds exactly two and `standings` stays empty.

A Test has three states here, and conflating them is what produces a wrong
article:

* no innings and no result line — not yet played, so a preview;
* a live status line ("Lunch, Day 2") where the verdict will go, or innings
  with no verdict at all — in progress or awaiting one, so **held** and picked
  up on a later run, because neither a preview nor a report is true of it yet;
* a result line — a report. Note that a drawn Test is a complete result with no
  winner, so unlike `cpl_matches` an empty `winner` must not disqualify it.
"""

import re

from bs4 import BeautifulSoup

from src import fetcher

WIKI_ARTICLE_ROOT = "https://en.wikipedia.org/wiki"

# One entry per tour whose Test series we cover. Adding the next tour is one
# line: the article's Test section is rendered from the same template, so
# nothing below is tour-specific.
TOURS = [
    {
        "article": "Pakistani_cricket_team_in_England_in_2026",
        "competition": "Pakistan tour of England 2026",
        "championship": "2025–2027 ICC World Test Championship",
        # The template names the side batting first on the left, which flips
        # between Tests of the same series — so `teams` is ordered host-first
        # instead, and the batting order kept in `scorecard.battingFirst`.
        # Omit for a series played at a neutral venue.
        "host": "England",
    },
]

FORMAT = "Test"
ATTRIBUTION = "Match data from Wikipedia, CC BY-SA 4.0"

# Only the Test series section is read. Tour articles commonly carry ODI and
# T20I sections built from the same template, and picking those up would file
# them under format "Test".
TEST_SECTION_RE = re.compile(r"^test[ _]series$", re.I)
TEST_MATCH_HEADING_RE = re.compile(r"\btest\b", re.I)

# "171 (48.1 overs)", "290/5d (120 overs)", "409" — the wickets, declaration
# marker and over count are each optional in the template.
TOTAL_RE = re.compile(r"^(\d+)(?:/(\d+))?\s*(d|dec)?\s*(?:\((\d+(?:\.\d+)?)\s*overs?\))?", re.I)
# "Abdullah Shafique 61 (104)", "Saud Shakeel 97* (122)"
BATTER_RE = re.compile(r"^(?P<name>.+?)\s+(?P<runs>\d+)\*?\s*\((?P<balls>\d+)\)$")
# "Josh Tongue 5/46 (9.1 overs)"
BOWLER_RE = re.compile(r"^(?P<name>.+?)\s+(?P<wickets>\d+)/(?P<conceded>\d+)\s*\((?P<overs>\d+(?:\.\d+)?)\s*overs?\)$")

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}


def fetch_recent_matches() -> list[dict]:
    documents = []
    for tour in TOURS:
        documents.extend(_fetch_tour(tour))
    return documents


def _fetch_tour(tour: dict) -> list[dict]:
    article_url = f"{WIKI_ARTICLE_ROOT}/{tour['article']}"
    soup = BeautifulSoup(fetcher.get(article_url).text, "lxml")

    sections = _test_match_sections(soup)
    if not sections:
        print(f"  no Test series section in {tour['article']} — template may have changed")
        return []

    documents = []
    # Wins going into each match, so a report never quotes a series scoreline
    # that only became true afterwards.
    wins: dict[str, int] = {}
    drawn = 0

    for section in sections:
        document = _parse_match(
            section,
            tour,
            article_url,
            matches_in_series=len(sections),
            series_before={"matchesPlayed": sum(wins.values()) + drawn, "wins": dict(wins), "drawn": drawn},
        )
        if document is None:
            continue

        documents.append(document)

        scorecard = document["scorecard"]
        if scorecard.get("status") != "completed":
            continue
        winner = scorecard.get("winner")
        if winner:
            wins[winner] = wins.get(winner, 0) + 1
        else:
            drawn += 1

    return documents


def _test_match_sections(soup) -> list:
    """The per-Test subsections of the article's Test series section.

    Parsoid wraps each heading's content in a `<section>`, so the Test series
    section physically contains its "1st Test"/"2nd Test" children — which is
    what keeps a tour's ODI boxes out of this list.
    """
    for section in soup.find_all("section"):
        heading = section.find(["h2", "h3", "h4"])
        if not heading:
            continue
        anchor = heading.get("id") or ""
        if not TEST_SECTION_RE.match(anchor) and not TEST_SECTION_RE.match(
            heading.get_text(strip=True)
        ):
            continue
        return [
            child
            for child in section.find_all("section")
            if child.select_one("table.vevent")
            and TEST_MATCH_HEADING_RE.search(_heading_text(child))
        ]
    return []


def _heading_text(section) -> str:
    heading = section.find(["h2", "h3", "h4"])
    return heading.get_text(strip=True) if heading else ""


def _host_first(teams: list[str], host: str) -> list[str]:
    """Name the sides the same way in every Test of a series.

    Only the display order of `teams` — which side batted first is read from
    the scoreline's own columns, so this cannot misattribute an innings.
    """
    if host and host in teams:
        return [host] + [team for team in teams if team != host]
    return teams


def _tidy(text: str) -> str:
    return re.sub(r"\s+([,.;)])", r"\1", re.sub(r"\s+", " ", text)).strip()


def _lines(node) -> list[str]:
    """The element's text, split on its `<br>` boundaries.

    The template packs several distinct facts into one cell separated only by
    line breaks (total, top scorer, top bowler; result, venue, umpires), so
    `get_text` alone would run them together.
    """
    clone = BeautifulSoup(str(node), "lxml")
    for br in clone.find_all("br"):
        br.replace_with("\n")
    return [line for line in (_tidy(part) for part in clone.get_text().split("\n")) if line]


def _parse_match(
    section, tour: dict, article_url: str, matches_in_series: int, series_before: dict
) -> dict | None:
    scorecard_table = section.select_one("table.vevent")
    box = scorecard_table.find_parent("div") or scorecard_table.parent

    # Footnote superscripts sit inside the date and notes text; left in they
    # turn "19–21 August 2026" into "19–21 August 2026[b]".
    for ref in box.select("sup.mw-ref"):
        ref.decompose()

    label = _heading_text(section)
    teams = [cell.get_text(strip=True) for cell in scorecard_table.select("td.vcard.attendee")]
    if len(teams) != 2 or not all(teams):
        print(f"  skipped {label}: expected 2 teams, got {teams}")
        return None
    teams = _host_first(teams, tour.get("host", ""))

    header, result_table, notes_block = _classify_box(box, scorecard_table)
    dates = _lines(header)[0] if header else ""
    start_date = _start_date(dates)
    if not start_date:
        print(f"  skipped {label}: unparseable date {dates!r}")
        return None

    innings = _parse_innings(scorecard_table)
    result, status_line, venue_line, umpires, player_of_the_match = _parse_result(
        result_table
    )

    # Played but not yet decided. Reporting it would need an outcome the source
    # hasn't published, and previewing it would deny innings that already
    # exist — so leave it `new` for a later run. A live status line settles this
    # on its own; innings-without-a-verdict catches a Test whose status line
    # the box omits.
    if status_line or (innings and not result):
        print(
            f"  holding {label}: {teams[0]} v {teams[1]} — in progress "
            f"({status_line or 'no result line yet'})"
        )
        return None

    ground, city = _split_venue(venue_line)
    completed = bool(result)

    scorecard = {
        "status": "completed" if completed else "upcoming",
        "match": label,
        "matchNumber": _match_number(label),
        "matchesInSeries": matches_in_series,
        "championship": tour.get("championship", ""),
        "dates": dates,
        "ground": ground,
        "city": city,
        "umpires": umpires,
        "notes": _lines(notes_block) if notes_block else [],
        # Series scoreline as it stood *before* this Test, so a report on the
        # 2nd Test doesn't quote the state after the 3rd.
        "seriesBefore": series_before,
        "attribution": ATTRIBUTION,
        "referenceUrl": article_url,
        "scorecardUrl": _scorecard_link(header),
    }

    if innings:
        scorecard["innings"] = innings
        # The template lists the side batting first on the left, which is not
        # always the home side.
        scorecard["battingFirst"] = innings[0]["team"]

    if completed:
        scorecard["result"] = result
        # Empty on a draw, a tie or an abandonment: those are finished Tests
        # with no winner, and the result line above says which.
        scorecard["winner"] = _winner(result, teams)
        if player_of_the_match:
            scorecard["playerOfTheMatch"] = player_of_the_match

    return {
        "sport": "cricket",
        # The section anchor is stable across the article's lifetime and is a
        # real page a reader can open at the right match; `save_raw_match`
        # upserts on it.
        "sourceUrl": f"{article_url}#{_anchor(section)}",
        "matchTitle": f"{label}: {teams[0]} vs {teams[1]}",
        "competition": tour["competition"],
        "teams": teams,
        "venue": ", ".join(part for part in (ground, city) if part),
        # ISO rather than the displayed range: the admin app's preview horizon
        # runs `Date.parse` over this, and "9–13 September 2026" yields NaN,
        # which silently drops an upcoming Test from generation. The human
        # range is kept in `scorecard.dates`.
        "date": start_date,
        "format": FORMAT,
        "scorecard": scorecard,
        "playerPerformances": _performances(scorecard_table),
        # Head-to-head sport: the outcome lives in the result line.
        "standings": [],
        # The only images in the box are flag icons; not recorded.
        "sourceImages": [],
    }


def _classify_box(box, scorecard_table):
    """Split the match box into its header, result and notes blocks.

    The template renders four siblings in a fixed order — a date/scorecard-link
    table, the `vevent` scoreline, a result/venue/umpires table, and a notes
    div — so each is identified by its position relative to the `vevent`
    rather than by a class, of which the last three carry none.
    """
    header = result_table = notes_block = None
    seen_scorecard = False

    for child in box.find_all(recursive=False):
        if child is scorecard_table:
            seen_scorecard = True
            continue
        if child.name == "table":
            if seen_scorecard:
                result_table = result_table or child
            else:
                header = header or child
        elif child.name == "div" and seen_scorecard:
            notes_block = notes_block or child

    return header, result_table, notes_block


def _scorecard_link(header) -> str:
    if not header:
        return ""
    link = header.select_one('a[href^="http"]')
    return link["href"] if link else ""


def _anchor(section) -> str:
    heading = section.find(["h2", "h3", "h4"])
    anchor = (heading.get("id") if heading else "") or ""
    return anchor or re.sub(r"[^A-Za-z0-9]+", "_", _heading_text(section)).strip("_")


def _innings_cells(scorecard_table) -> list:
    """Innings cells in the order they were bowled.

    The template lays the two sides out in columns and the innings in rows, so
    reading row by row and left to right gives first innings, reply, third
    innings, fourth — the order a report has to narrate them in.
    """
    cells = []
    for row in scorecard_table.select("tr"):
        if row.select("td.vcard.attendee"):
            continue  # the team-name header row
        for cell in row.find_all("td"):
            if cell.get_text(strip=True):
                cells.append(cell)
    return cells


def _parse_innings(scorecard_table) -> list[dict]:
    teams = [cell.get_text(strip=True) for cell in scorecard_table.select("td.vcard.attendee")]
    innings = []

    for cell in _innings_cells(scorecard_table):
        lines = _lines(cell)
        if not lines:
            continue
        total = TOTAL_RE.match(lines[0])
        if not total:
            continue

        runs, wickets, declared, overs = total.groups()
        # Column position names the batting side: left cell is teams[0].
        column = len(cell.find_previous_siblings("td"))
        entry = {
            "team": teams[0] if column == 0 else teams[-1],
            "order": len(innings) + 1,
            "runs": int(runs),
        }
        if wickets is not None:
            entry["wickets"] = int(wickets)
        if declared:
            entry["declared"] = True
        if overs is not None:
            entry["overs"] = float(overs)

        # Kept verbatim: "5/46 (9.1 overs)" is how a scorecard states a bowling
        # figure, and splitting it across the flat PlayerPerformance fields
        # would drop the runs conceded.
        if len(lines) > 1:
            entry["topScorer"] = lines[1]
        if len(lines) > 2:
            entry["topBowler"] = lines[2]

        innings.append(entry)

    return innings


def _performances(scorecard_table) -> list[dict]:
    """The top scorer and top bowler the template names for each innings.

    Only what the source states: a batter gets runs and balls, a bowler gets
    wickets and overs. A bowler's runs conceded is deliberately not written to
    `runs` — the field means runs scored, and the two are indistinguishable
    once flattened. The full figure stays in `scorecard.innings[].topBowler`.
    """
    performances = []
    for cell in _innings_cells(scorecard_table):
        for line in _lines(cell)[1:]:
            batter = BATTER_RE.match(line)
            if batter:
                performances.append(
                    {
                        "name": batter.group("name"),
                        "runs": int(batter.group("runs")),
                        "balls": int(batter.group("balls")),
                    }
                )
                continue
            bowler = BOWLER_RE.match(line)
            if bowler:
                performances.append(
                    {
                        "name": bowler.group("name"),
                        "wickets": int(bowler.group("wickets")),
                        "overs": float(bowler.group("overs")),
                    }
                )
    return performances


# A finished Test's bolded line is a verdict. A Test still being played has a
# bolded *status* in the same slot instead ("Lunch, Day 2", "Stumps, Day 3"),
# and reading that as a result marks a live match `completed` — which is how a
# mid-match Test came to carry `result: "Lunch, Day 2"`. Matching verdict
# shapes rather than blacklisting status phrases means an unfamiliar status
# line is held rather than reported.
_VERDICT_RE = re.compile(
    r"\bwon by\b|\bmatch drawn\b|\bmatch tied\b|\bdrawn\b|\btied\b"
    r"|\babandoned\b|\bno result\b",
    re.IGNORECASE,
)


def _is_verdict(line: str) -> bool:
    return bool(line) and bool(_VERDICT_RE.search(line))


def _parse_result(result_table) -> tuple[str, str, str, list[str], str]:
    """Verdict, live-status line, venue line, umpires and player of the match.

    The bolded line is the one signal in the box that distinguishes a finished
    Test from a scheduled one — but it holds a live status while the Test is
    being played, so it is only returned as `result` when it reads as a
    verdict. Otherwise it comes back as `status_line`, which tells the caller
    the Test is underway rather than unplayed.
    """
    if result_table is None:
        return "", "", "", [], ""

    bold = result_table.find("b")
    bold_line = _tidy(bold.get_text()) if bold else ""
    result = bold_line if _is_verdict(bold_line) else ""
    status_line = "" if result else bold_line

    venue_line = ""
    umpires: list[str] = []
    player_of_the_match = ""

    for line in _lines(result_table):
        if line == bold_line:
            continue
        if line.lower().startswith("umpires:"):
            umpires = [
                part
                for part in (
                    _tidy(chunk) for chunk in re.split(r"\s+and\s+|,", line.split(":", 1)[1])
                )
                if part
            ]
        elif line.lower().startswith("player of the match:"):
            player_of_the_match = _tidy(line.split(":", 1)[1])
        elif not venue_line:
            venue_line = line

    return result, status_line, venue_line, umpires, player_of_the_match


def _split_venue(venue_line: str) -> tuple[str, str]:
    if not venue_line:
        return "", ""
    ground, _, city = venue_line.partition(",")
    return _tidy(ground), _tidy(city)


def _match_number(label: str) -> int | None:
    match = re.match(r"\s*(\d+)", label)
    return int(match.group(1)) if match else None


def _significant_words(name: str) -> set[str]:
    noise = {"and", "the", "of"}
    words = "".join(c if c.isalnum() or c.isspace() else " " for c in name.lower()).split()
    return {word for word in words if word not in noise}


def _winner(result: str, teams: list[str]) -> str:
    """Name the winner only when the result line unambiguously points at one
    side. "Match drawn" and "Match abandoned" name nobody, and the winner is
    never inferred from the totals — a side can be bowled out for less and
    still win a Test by an innings."""
    if " won " not in result:
        return ""
    said = _significant_words(result.split(" won ")[0])
    hits = [team for team in teams if _significant_words(team) & said]
    return hits[0] if len(hits) == 1 else ""


def _date_parts(text: str) -> dict:
    day = re.search(r"\b(\d{1,2})\b", text)
    year = re.search(r"\b(\d{4})\b", text)
    month = next((number for name, number in MONTHS.items() if name in text.lower()), None)
    return {
        "day": int(day.group(1)) if day else None,
        "month": month,
        "year": int(year.group(1)) if year else None,
    }


def _start_date(label: str) -> str:
    """ISO start date from a displayed range like "19–21 August 2026".

    The month and year are printed once for a range inside a single month, so
    whatever the start half omits is taken from the end half. A range that
    crosses a year prints the year on both halves, so the start's own value
    wins where it has one.
    """
    parts = [part for part in (p.strip() for p in re.split(r"[–—-]", label)) if part]
    if not parts:
        return ""

    start, end = _date_parts(parts[0]), _date_parts(parts[-1])
    day = start["day"] or end["day"]
    month = start["month"] or end["month"]
    year = start["year"] or end["year"]
    if not (day and month and year):
        return ""
    return f"{year}-{month:02d}-{day:02d}"
