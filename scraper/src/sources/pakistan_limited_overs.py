"""Pakistan's ODI and T20I matches, from the Wikipedia series articles.

The sibling of `pakistan_tests`, and for the same reason: there is no keyless
factual feed for international cricket, and `/wiki/<Article>` is allowed for
the wildcard user agent. Text is CC BY-SA 4.0, credited through
`scorecard.attribution`.

Limited-overs matches are rendered from a different template than Tests
("Single-innings cricket match"), and it carries no `vevent` class or `vcard`
cells, so `pakistan_tests`' selectors find nothing here. Each match box is
instead a Parsoid transclusion whose `data-mw` attribute holds the template's
own named parameters (`team1`, `score1`, `runs1`, `result`, `venue`, ...).
Those are read directly: they are the facts the editors typed, without the
layout the box wraps them in. Team names still come from the rendered box,
since the parameter is a flag template (`{{cr-rt|PAK}}`) rather than a name.

Only played matches are stored. An unplayed fixture is skipped rather than
previewed, for two reasons: the pipeline never turns a preview into a report
once results land (a `processed` raw_match is never revisited), so a preview
would block the report; and fixture boxes are typed in ahead of time and go
wrong — the tri-series article listed the 4th and 5th ODI opponents swapped
against the published schedule. A box with a verdict has been checked against
the match itself. A score without a verdict is held for a later run, as in
`pakistan_tests`.
"""

import json
import re

from bs4 import BeautifulSoup

from src import fetcher
from src.sources.pakistan_tests import (
    ATTRIBUTION,
    BATTER_RE,
    BOWLER_RE,
    TOTAL_RE,
    WIKI_ARTICLE_ROOT,
    _host_first,
    _is_verdict,
    _match_number,
    _split_venue,
    _start_date,
    _tidy,
    _winner,
)

TEMPLATE = "Single-innings cricket match"
FOCUS_TEAM = "Pakistan"

# One entry per series. A tri-series lists every side's matches, but only
# Pakistan's are covered — the series scoreline still counts all of them.
SERIES = [
    {
        "article": "Sri_Lankan_cricket_team_in_Pakistan_in_2026–27",
        "competition": "Sri Lanka tour of Pakistan 2026–27",
        "format": "T20",
        "host": "Pakistan",
    },
    {
        "article": "2026–27_Pakistan_ODI_Tri-Series",
        "competition": "2026–27 Pakistan ODI Tri-Series",
        "format": "ODI",
        "host": "Pakistan",
    },
]

# Keeps an article's Test boxes, which sometimes use this template for warm-up
# fixtures, out of a series filed under ODI or T20.
FORMAT_HEADING_RE = {
    "T20": re.compile(r"\bT20I?\b", re.I),
    "ODI": re.compile(r"\bODI\b|\bfinal\b", re.I),
}


def fetch_recent_matches() -> list[dict]:
    documents = []
    for series in SERIES:
        documents.extend(_fetch_series(series))
    return documents


def _fetch_series(series: dict) -> list[dict]:
    article_url = f"{WIKI_ARTICLE_ROOT}/{series['article']}"
    soup = BeautifulSoup(fetcher.get(article_url).text, "lxml")

    boxes = _match_boxes(soup, FORMAT_HEADING_RE[series["format"]])
    if not boxes:
        print(f"  no {series['format']} match boxes in {series['article']} — template may have changed")
        return []

    documents = []
    # Wins going into each match, so a report never quotes a scoreline that
    # only became true afterwards.
    wins: dict[str, int] = {}
    no_result = 0

    for label, anchor, box, params in boxes:
        document = _parse_match(
            label,
            anchor,
            box,
            params,
            series,
            article_url,
            matches_in_series=len(boxes),
            series_before={
                "matchesPlayed": sum(wins.values()) + no_result,
                "wins": dict(wins),
                "noResult": no_result,
            },
        )
        if document is None:
            continue

        scorecard = document["scorecard"]
        if scorecard["status"] == "completed":
            if scorecard.get("winner"):
                wins[scorecard["winner"]] = wins.get(scorecard["winner"], 0) + 1
            else:
                no_result += 1

        if FOCUS_TEAM in document["teams"]:
            documents.append(document)

    return documents


def _match_boxes(soup, heading_re) -> list[tuple[str, str, object, dict]]:
    """(heading label, anchor, box element, template params) per match, in
    article order. A box is matched by the template it was rendered from, so
    the points table and fixtures summary never qualify."""
    boxes = []
    for box in soup.select('[typeof~="mw:Transclusion"][data-mw]'):
        params = _template_params(box.get("data-mw", ""))
        if params is None:
            continue
        heading = box.find_previous(["h2", "h3", "h4"])
        label = heading.get_text(strip=True) if heading else ""
        if not heading_re.search(label):
            continue
        anchor = (heading.get("id") if heading else "") or re.sub(r"[^A-Za-z0-9]+", "_", label)
        boxes.append((label, anchor, box, params))
    return boxes


def _template_params(data_mw: str) -> dict | None:
    try:
        parts = json.loads(data_mw).get("parts", [])
    except (ValueError, AttributeError):
        return None
    for part in parts:
        template = part.get("template") if isinstance(part, dict) else None
        if not template:
            continue
        if template.get("target", {}).get("wt", "").strip() != TEMPLATE:
            continue
        return {key: value.get("wt", "") for key, value in template.get("params", {}).items()}
    return None


def _plain(wikitext: str) -> str:
    """Wikitext to the text a reader sees: links to their label, external links
    to their caption, templates and references dropped."""
    text = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", wikitext, flags=re.S)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", text)
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return text


def _plain_lines(wikitext: str) -> list[str]:
    return [line for line in (_tidy(part.lstrip("*# ")) for part in _plain(wikitext).split("\n")) if line]


def _teams(box) -> list[str]:
    """The two side names, from the bold team cells of the scoreline table.

    The table is the one holding the "v" between the sides; its first row's
    outer cells each open with the team name in bold.
    """
    for table in box.find_all("table"):
        row = table.find("tr")
        cells = row.find_all("td", recursive=False) if row else []
        if len(cells) == 3 and cells[1].get_text(strip=True) == "v":
            names = [cell.find("b").get_text(strip=True) if cell.find("b") else "" for cell in (cells[0], cells[2])]
            return [_tidy(name) for name in names]
    return []


def _innings(params: dict, teams_in_order: list[str]) -> list[dict]:
    """The template names the side batting first as `team1` and its total as
    `score1`, so parameter order is batting order."""
    innings = []
    for side, team in zip(("1", "2"), teams_in_order):
        score = _tidy(_plain(params.get(f"score{side}", "")))
        total = TOTAL_RE.match(score) if score else None
        if not total:
            continue
        runs, wickets, _, overs = total.groups()
        entry = {"team": team, "order": len(innings) + 1, "runs": int(runs)}
        if wickets is not None:
            entry["wickets"] = int(wickets)
        if overs is not None:
            entry["overs"] = float(overs)
        # In this template `runs1`/`wickets1` sit under team1's column: team1's
        # best batter, and the best bowler *against* team1.
        batters = _plain_lines(params.get(f"runs{side}", ""))
        bowlers = _plain_lines(params.get(f"wickets{side}", ""))
        if batters:
            entry["topScorer"] = batters[0]
        if bowlers:
            entry["topBowler"] = bowlers[0]
        innings.append(entry)
    return innings


def _performances(params: dict) -> list[dict]:
    performances = []
    for key in ("runs1", "wickets1", "runs2", "wickets2"):
        for line in _plain_lines(params.get(key, "")):
            batter = BATTER_RE.match(line)
            if batter:
                performances.append(
                    {"name": batter.group("name"), "runs": int(batter.group("runs")), "balls": int(batter.group("balls"))}
                )
                continue
            bowler = BOWLER_RE.match(line)
            if bowler:
                performances.append(
                    {"name": bowler.group("name"), "wickets": int(bowler.group("wickets")), "overs": float(bowler.group("overs"))}
                )
    return performances


def _parse_match(
    label, anchor, box, params, series, article_url, matches_in_series, series_before
) -> dict | None:
    batting_order = _teams(box)
    if len(batting_order) != 2 or not all(batting_order) or "TBD" in batting_order:
        print(f"  skipped {label}: teams not yet known ({batting_order})")
        return None
    teams = _host_first(batting_order, series.get("host", ""))

    dates = _tidy(_plain(params.get("date", "")))
    start_date = _start_date(dates)
    if not start_date:
        print(f"  skipped {label}: unparseable date {dates!r}")
        return None

    innings = _innings(params, batting_order)
    result_line = _tidy(_plain(params.get("result", "")))
    result = result_line if _is_verdict(result_line) else ""

    if (result_line and not result) or (innings and not result):
        print(f"  holding {label}: {teams[0]} v {teams[1]} — in progress ({result_line or 'no result line yet'})")
        return None

    if not result:
        return None  # unplayed: see the module docstring

    ground, city = _split_venue(_tidy(_plain(params.get("venue", ""))))
    completed = True
    notes = _plain_lines(params.get("toss", "")) + _plain_lines(params.get("rain", "")) + _plain_lines(params.get("notes", ""))

    scorecard = {
        "status": "completed" if completed else "upcoming",
        "match": label,
        "matchNumber": _match_number(label),
        "matchesInSeries": matches_in_series,
        "dates": dates,
        "localTime": _tidy(_plain(params.get("time", ""))),
        # The template flags a day/night match with `daynight` and a night
        # match with `night`; neither set is a day match.
        "session": "day/night" if params.get("daynight", "").strip()
        else "night" if params.get("night", "").strip()
        else "day",
        "ground": ground,
        "city": city,
        "umpires": [u for u in (_tidy(p) for p in re.split(r"\s+and\s+|,", _plain(params.get("umpires", "")))) if u],
        "notes": notes,
        "seriesBefore": series_before,
        "attribution": ATTRIBUTION,
        "referenceUrl": article_url,
        "scorecardUrl": _scorecard_url(params.get("report", "")),
    }

    if innings:
        scorecard["innings"] = innings
        scorecard["battingFirst"] = innings[0]["team"]

    if completed:
        scorecard["result"] = result
        scorecard["winner"] = _winner(result, teams)
        motm = _tidy(_plain(params.get("motm", "")))
        if motm:
            scorecard["playerOfTheMatch"] = motm

    return {
        "sport": "cricket",
        "sourceUrl": f"{article_url}#{anchor}",
        "matchTitle": f"{label}: {teams[0]} vs {teams[1]}",
        "competition": series["competition"],
        "teams": teams,
        "venue": ", ".join(part for part in (ground, city) if part),
        "date": start_date,
        "format": series["format"],
        "scorecard": scorecard,
        "playerPerformances": _performances(params),
        "standings": [],
        "sourceImages": [],
    }


def _scorecard_url(report: str) -> str:
    link = re.search(r"https?://\S+", report)
    return link.group(0).rstrip("]") if link else ""
