"""HTTP layer for the scraper.

Scrapy gives you robots.txt compliance, retry-with-backoff and throttling as
downloader middlewares. We scrape one small site on a 6-hourly cron, so pulling
in Twisted and the whole framework isn't worth it — but those three behaviours
are worth having, so they're implemented here directly.

If this ever grows to many sites or thousands of pages, switch to Scrapy rather
than extending this module: its scheduler, dedup fingerprinting and concurrency
handling are the parts that get genuinely hard to reimplement.
"""

import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

from src.config import DELAY_BETWEEN_REQUESTS_SECONDS, REQUEST_TIMEOUT_SECONDS, USER_AGENT

MAX_ATTEMPTS = 3
RETRY_STATUSES = {429, 500, 502, 503, 504}

_robots_cache: dict[str, RobotFileParser] = {}
_last_request_at = 0.0


class ScrapeBlocked(Exception):
    """Raised when robots.txt disallows the URL for our user agent."""


def _robots_for(url: str) -> RobotFileParser:
    root = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    if root not in _robots_cache:
        parser = RobotFileParser()
        parser.set_url(f"{root}/robots.txt")
        try:
            # Deliberately not `parser.read()`: that fetches through urllib,
            # which announces itself as "Python-urllib/3.x". Wikipedia 403s
            # that agent, and RobotFileParser reads a 403 on robots.txt as
            # "everything is disallowed" — so a site that allows us was being
            # skipped over the identity of the robots request itself. The rules
            # have to be read with the same User-Agent the scrape will use.
            response = requests.get(
                f"{root}/robots.txt",
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code in (401, 403):
                # Access to the rules is refused; that is not permission.
                parser.disallow_all = True
            elif 400 <= response.status_code < 500:
                # No robots.txt published, so nothing is disallowed.
                parser.allow_all = True
            else:
                response.raise_for_status()
                # Wikipedia serves robots.txt with a BOM, which would otherwise
                # become part of the first directive.
                parser.parse(response.text.lstrip("﻿").splitlines())
        except requests.RequestException:
            # An unreadable robots.txt is not permission — treat as disallowed.
            parser.disallow_all = True
        _robots_cache[root] = parser
    return _robots_cache[root]


def assert_allowed(url: str) -> None:
    if not _robots_for(url).can_fetch(USER_AGENT, url):
        raise ScrapeBlocked(f"robots.txt disallows {url}")


def _throttle() -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < DELAY_BETWEEN_REQUESTS_SECONDS:
        time.sleep(DELAY_BETWEEN_REQUESTS_SECONDS - elapsed)
    _last_request_at = time.monotonic()


def get(url: str) -> requests.Response:
    """Fetch a URL, honouring robots.txt, throttling and retrying on 5xx/429."""
    assert_allowed(url)

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        _throttle()
        try:
            response = requests.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code in RETRY_STATUSES:
                raise requests.HTTPError(f"status {response.status_code}")
            response.raise_for_status()
            return response
        except (requests.RequestException, requests.HTTPError) as err:
            last_error = err
            if attempt == MAX_ATTEMPTS:
                break
            backoff = 2**attempt
            print(f"  {url} attempt {attempt} failed ({err}) — retrying in {backoff}s")
            time.sleep(backoff)

    raise RuntimeError(f"failed to fetch {url}: {last_error}")
