import type { Sport } from "@cricket-blog/types";

/**
 * Site-wide branding in one place. The masthead, footer, page titles and
 * JSON-LD publisher all read from here, so renaming the publication is a
 * single-line change rather than a grep across components.
 */
/**
 * Absolute origin the site is served from — the base every canonical, OG URL
 * and sitemap entry is built on.
 *
 * Resolution order matters. `BLOG_BASE_URL` wins so a custom domain can be
 * pinned. Failing that, Vercel injects the project's *production* host, which
 * is what keeps preview deployments from advertising their own throwaway URL as
 * canonical. Localhost is the dev fallback only.
 *
 * This is not cosmetic: a canonical or sitemap entry pointing at localhost is a
 * URL Google cannot fetch, and it drops the page rather than indexing it. That
 * is exactly what shipped before this helper existed, so resolve the base HERE
 * and nowhere else — an inline `process.env.BLOG_BASE_URL ?? "http://localhost:3000"`
 * is the bug, not the pattern.
 */
export function siteUrl(): string {
  const configured = process.env.BLOG_BASE_URL;
  if (configured) return configured.replace(/\/$/, "");

  const vercelHost = process.env.VERCEL_PROJECT_PRODUCTION_URL;
  if (vercelHost) return `https://${vercelHost}`;

  return "http://localhost:3000";
}

export const SITE_NAME = "Sporting Beat";

export const SITE_TAGLINE =
  "Race reports, match previews and analysis written from the official record.";

export interface SportMeta {
  /** URL segment for /sports/<slug>. */
  slug: string;
  label: string;
  /** Shown in the masthead dateline strip and on the sport landing page. */
  competition: string;
  blurb: string;
  /** Noun for a single event, used in headings and empty states. */
  eventNoun: string;
  /** Plural label for the schedule panel in the sidebar. */
  scheduleLabel: string;
  /** Source credit the data licence asks for; rendered on sport pages. */
  dataCredit?: { label: string; href: string };
}

export const SPORT_META: Record<Sport, SportMeta> = {
  cricket: {
    slug: "cricket",
    label: "Cricket",
    competition: "Caribbean Premier League · 2026",
    blurb:
      "Match-by-match coverage of the Caribbean Premier League and Pakistan's Test tours, written from the published scorecards — plus the Asian Legends League schedule.",
    eventNoun: "match",
    scheduleLabel: "Season fixtures",
    dataCredit: {
      label: "Caribbean Premier League data via TheSportsDB",
      href: "https://www.thesportsdb.com/league/5176",
    },
  },
  motorsport: {
    slug: "formula-1",
    label: "Formula 1",
    competition: "Formula 1 · 2026 season",
    blurb:
      "Round-by-round Formula 1 reports built from official classifications — finishing order, retirements and the championship picture.",
    eventNoun: "race",
    scheduleLabel: "Race calendar",
    dataCredit: {
      label: "Race data via the Jolpica F1 API",
      href: "https://api.jolpi.ca/ergast/f1",
    },
  },
};

/** Sports in masthead / landing-page order. */
export const SPORT_ORDER: Sport[] = ["motorsport", "cricket"];

const BY_SLUG = new Map<string, Sport>(
  (Object.entries(SPORT_META) as [Sport, SportMeta][]).map(([sport, meta]) => [meta.slug, sport])
);

export function sportFromSlug(slug: string): Sport | null {
  return BY_SLUG.get(slug) ?? null;
}

export function sportHref(sport: Sport): string {
  return `/sports/${SPORT_META[sport].slug}`;
}
