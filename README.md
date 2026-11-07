# Sporting Beat

AI-automated sports blog, currently covering **Formula 1** and **cricket**. A
Python scraper collects raw event facts, Gemini turns them into original articles
+ cover images, an admin app reviews/publishes them, and a Next.js blog serves
the published posts with SSG/ISR.

Adding a sport means four things, and skipping any of them produces
sport-blind output rather than an error:

1. an adapter in `scraper/src/sources/` that returns `sport: "<name>"`
2. the value added to `Sport` in `packages/types`, the `sport` enums in
   `packages/db/src/models/`, and `VALID_SPORTS`/`VALID_FORMATS` in
   `scraper/src/items.py`
3. a `SPORT_BRIEFS` entry in `packages/ai/src/gemini.ts` (prompt voice) and a
   `SCENE_QUERIES` entry in `packages/ai/src/pexels.ts` (cover imagery)
4. a `SPORT_META` entry in `apps/blog/lib/site.ts` (labels, URL slug, blurb,
   data credit)

Head-to-head sports fill `teams` with exactly two names. Field sports (a race
grid) leave `teams` empty and put the finishing order in `standings` instead —
`hasResults()` in `packages/types` is what decides preview vs report, not the
presence of `playerPerformances`.

## Structure

```
apps/admin   - review/edit/approve dashboard (Auth.js login), port 3011
apps/blog    - public blog, SSG + on-demand revalidation, port 3000
packages/db  - Mongoose connection + schemas (raw_matches, posts, admin_users)
packages/types - shared TypeScript types
packages/ai  - Gemini text/image generation + Cloudinary upload
scraper/     - Python scraper (runs locally, or via GitHub Actions cron in prod)
```

## First-time setup

```bash
pnpm install

# create your admin login (email/password)
cd apps/admin && pnpm seed-admin you@example.com "your-password" && cd ../..
```

Fill in the missing Cloudinary values in `apps/admin/.env.local` and
`apps/blog/.env.local` (`CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_SECRET`).

## Running locally

```bash
pnpm dev   # runs both apps via turbo (blog on :3000, admin on :3011)
```

The admin port is 3011 rather than 3001 because a sibling project on this
machine holds 3001 and 3002. Three places must agree if you change it:
`apps/admin/package.json` (`dev` script), `AUTH_URL` in
`apps/admin/.env.local`, and `ADMIN_BASE_URL` in `scraper/.env`. A stale
`AUTH_URL` doesn't fail loudly — login just redirects to a dead port.

Scraper (separate, Python):

```bash
cd scraper
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python main.py
```

### Data sources

**Formula 1 — `scraper/src/sources/f1_races.py`** (the productive source).
Reads the [Jolpica](https://api.jolpi.ca/ergast/f1) JSON API, the maintained
successor to Ergast: a JSON contract rather than HTML, so nothing breaks when
the site is restyled. One race becomes one `raw_matches` document; completed
rounds carry the full classification (position, driver, constructor, finishing
time or retirement reason, points) so Gemini writes reports rather than
speculation.

Its `robots.txt` sets `Allow: /` for the wildcard agent alongside
`Content-Signal: search=yes,ai-train=no`. We honour `ai-train=no` — race facts
are reference input for original prose, never training data. The licence asks
for attribution, which is rendered on every race article and in the footer via
`SPORT_META.motorsport.dataCredit`, not merely stored. **Check the upstream
terms before running ads against this content**: factual results aren't
copyrightable, but Ergast-lineage data has carried non-commercial expectations.

Two API quirks the adapter handles: `limit` is capped at 100 regardless of what
you ask for (so a season's ~480 result rows need paging, and a single race can
straddle a page boundary), and championship standings are only quoted for the
*upcoming* race — printing today's table inside a report on round 3 would be an
anachronism.

**Cricket — `scraper/src/sources/allt20_fixtures.py`**, scraping
https://allt20.asia/fixtures (Asian Legends League Season 2). That site's
robots.txt allows all bots and its pages are server-rendered HTML.

Its `/results` page still says "No results yet" even though the published
schedule (30 Jul – 10 Aug 2026) has passed and every fixture is still flagged
`upcoming`, so only fixtures exist — no scorecards or player stats. All 15
cricket dispatches are therefore previews. The tournament also lists 21
fixtures, but 6 of those are ceremonies and playoff slots whose teams are still
`—`; the adapter skips them deliberately (`len(teams) < 2`) rather than letting
Gemini preview "Rank 1 vs Rank 2".

**Test cricket — `scraper/src/sources/pakistan_tests.py`**, reading the
Wikipedia tour article for each series in `TOURS` (currently Pakistan's 2026
tour of England). There is no keyless factual feed for international Tests:
TheSportsDB carries franchise T20 leagues and county cricket but no Test
competition for any country, and the two obvious sites are the ones already
ruled out below. `/wiki/<Article>` is explicitly allowed for the wildcard user
agent while `/w/api.php` and `/api/` are not, so this reads the rendered
article rather than the MediaWiki API. Text is CC BY-SA 4.0, credited per
article through `scorecard.attribution`.

A Test has three states in that template and they must not be conflated: no
innings and no result line is a fixture (preview); innings but no bolded result
line is a match in progress, which the adapter **holds** for a later run rather
than writing up either way; a result line is a report. Unlike `cpl_matches`, an
empty `winner` must not disqualify a result — a drawn Test is a complete
outcome with no winner.

Two things the adapter has to normalise. The template lists the side batting
first on the left, which flips between Tests of the same series, so `teams` is
ordered host-first (`TOURS[].host`) and the batting order kept in
`scorecard.battingFirst`. And `date` is stored as the ISO start date, not the
displayed "9–13 September 2026" range — the admin app's preview horizon runs
`Date.parse` over that field, and a range yields `NaN`, which silently drops an
upcoming Test from generation. The human range stays in `scorecard.dates`.
`scorecard.seriesBefore` carries the series scoreline as it stood *going into*
each Test, for the same anachronism reason the F1 adapter only quotes
standings for an upcoming race.

`example_source.py` is an unfilled template kept as a starting point for adding
another source. Note that ESPN Cricinfo (403s all automated requests via its
CDN) and Cricbuzz (`Disallow: /` for generic bots) were both checked and are
not scrapeable.

`src/fetcher.py` handles robots.txt compliance, throttling and retry/backoff —
the three Scrapy downloader-middleware behaviours worth having at this size. It
reads robots.txt with the scraper's own User-Agent rather than through
`RobotFileParser.read()`, which fetches as `Python-urllib/3.x`: Wikipedia 403s
that agent, and a 403 on robots.txt is read as "everything is disallowed", so a
site that allows us was being skipped over the identity of the robots request
itself. A genuine 403 still denies.
`src/items.py` validates each scrape before it reaches MongoDB (Scrapy's item
pipeline role). If this grows to many sites or thousands of pages, move to
Scrapy proper rather than extending these: its scheduler, request fingerprinting
and concurrency handling are the hard parts to reimplement.

## Content pipeline

1. Scraper seeds `raw_matches` (status: `new`) in MongoDB.
2. It (or a manual `curl`) triggers `POST /api/generate` on the admin app with
   header `x-cron-secret: $CRON_SECRET` — this runs Gemini text + image
   generation and writes `posts`. One call handles `BATCH_SIZE` (5) events, and
   **contested events are selected first** so a rate-limited free-tier batch is
   spent on reports rather than speculative previews.
3. Posts scoring `>= AUTO_PUBLISH_THRESHOLD` (0.85, see `apps/admin/lib/publish.ts`)
   auto-publish; everything else lands in the admin dashboard as `pending` for
   manual review (edit / approve / reject / regenerate).
4. Approving a post notifies the blog app (`POST /api/revalidate`) so the new
   page appears without a full redeploy.

### Cover images

`createCoverImage` tries three sources in order:

1. **Pexels** — commercially licensed stock, credited on the article and linked
   in the footer as the licence asks. Needs a free `PEXELS_API_KEY`.
2. **Gemini image generation + Cloudinary** — original artwork. Gemini's free
   tier grants **no image quota** (`limit: 0` on every image model, verified
   against the live API), so this needs a paid plan plus the two missing
   Cloudinary values (`CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_SECRET`).
3. **`/placeholder-cover.svg`** in each app's `public/`.

Cover scenes are chosen per sport (`SCENE_QUERIES` in `pexels.ts`) — Pexels has
no photography of this league or of a named driver, so the variety comes from
the kind of moment depicted. A missing entry silently yields cricket photos on
an F1 article.

Images from the scraped source site are **never** used as covers. The scraper
records their URLs in `raw_matches.sourceImages` as provenance only — they are
the tournament's copyrighted photography and its logos are also trademarks, and
editing them would produce a derivative work rather than avoid the rights issue.
That is the same reason the pipeline generates prose from facts instead of
rewriting source articles.

### Gemini output quirks

Two failure modes cost whole articles before they were handled, both in
`packages/ai/src/gemini.ts`:

- **`confidenceScore` ignores the requested scale.** The prompt asks for 0.0-1.0
  and the model has returned `1.9`, which the `Post` schema rejects outright
  (`max: 1`) — the article was generated, then thrown away. `normalizeConfidence`
  rescales the obvious 0-10 / percentage cases and clamps the rest; anything
  unparseable becomes 0, routing the post to manual review rather than
  auto-publishing on a score we don't trust.
- **Occasional runaway JSON.** Despite the response schema, the model sometimes
  degenerates into repetition and returns ~1.4 MB of unterminated JSON. A
  `SyntaxError` from `JSON.parse` is therefore treated as transient and retried
  alongside 429/503.

Model names are pinned in `packages/ai/src/gemini.ts`. Google retires models
for new API keys fairly aggressively — `gemini-2.5-flash` and
`imagen-4.0-generate-001` were both already unavailable during setup. If
generation starts 404ing, list what the key can actually reach:

```bash
curl -s "https://generativelanguage.googleapis.com/v1beta/models?key=$GEMINI_API_KEY"
```

## Deploying

- Two separate Vercel projects: `apps/admin` and `apps/blog` (set the Vercel
  project's root directory accordingly).
- Copy the env vars from each app's `.env.local` into that Vercel project's
  environment variables — `BLOG_BASE_URL` on the admin project must point at
  the deployed blog URL, and vice versa isn't needed but `REVALIDATE_SECRET`
  must match on both.
- GitHub Actions cron (`.github/workflows/scrape.yml`) needs repo secrets:
  `MONGODB_URI`, `ADMIN_BASE_URL` (deployed admin URL), `CRON_SECRET`.
- Repo should be public for unlimited free GitHub Actions minutes.
# blog_folder
