import { NextRequest, NextResponse } from "next/server";
import { connectToDatabase, RawMatchModel, PostModel } from "@cricket-blog/db";
import { generateArticle, createCoverImage } from "@cricket-blog/ai";
import { normalizeTags, sportOf, type RawMatch } from "@cricket-blog/types";
import { AUTO_PUBLISH_THRESHOLD, notifyBlogToRevalidate } from "@/lib/publish";

const BATCH_SIZE = 5;
// Gemini free tier has a per-minute request cap — space out generations
// within a batch instead of firing them concurrently.
const DELAY_BETWEEN_MATCHES_MS = 4000;

// Events that have actually been contested carry real results, so they yield
// reports rather than speculative previews. On a rate-limited free tier the
// batch is the scarce resource, so spend it on those first.
// Kept in step with `hasResults` in @cricket-blog/types — a source whose
// results live only in `scorecard` (CPL team totals) is contested too.
const CONTESTED = {
  $or: [
    { "standings.0": { $exists: true } },
    { "playerPerformances.0": { $exists: true } },
    { "scorecard.status": "completed" },
  ],
};

// A preview written months out says nothing a reader can use and nothing Google
// will rank — a 23-round season would otherwise yield 22 speculative pieces the
// day it's scraped. Events past this horizon stay `new` until they're closer, at
// which point they're either previewable or already contested.
const PREVIEW_HORIZON_DAYS = 21;

/** The scraped `date` is a display string, and its format differs per source
 *  ("2026-08-23" from the F1 API, "30 JUL 2026" from the cricket scrape). Both
 *  are Date-parseable; anything that isn't is treated as out of range rather
 *  than silently previewed. */
function isWithinPreviewHorizon(match: RawMatch, now: number): boolean {
  const eventTime = Date.parse(String(match.date));
  if (Number.isNaN(eventTime)) return false;
  return eventTime - now <= PREVIEW_HORIZON_DAYS * 24 * 60 * 60 * 1000;
}

async function selectBatch(): Promise<RawMatch[]> {
  const contested = (await RawMatchModel.find({ status: "new", ...CONTESTED })
    .limit(BATCH_SIZE)
    .lean()) as unknown as RawMatch[];

  if (contested.length >= BATCH_SIZE) return contested;

  // Fetched unfiltered because the horizon check can't be expressed against a
  // display-string date in the query itself.
  const candidates = (await RawMatchModel.find({
    status: "new",
    $nor: [CONTESTED],
  }).lean()) as unknown as RawMatch[];

  const now = Date.now();
  const upcoming = candidates
    .filter((match) => isWithinPreviewHorizon(match, now))
    .slice(0, BATCH_SIZE - contested.length);

  return [...contested, ...upcoming];
}

export async function POST(request: NextRequest) {
  const secret = request.headers.get("x-cron-secret");
  if (!secret || secret !== process.env.CRON_SECRET) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  await connectToDatabase();
  const rawMatches = await selectBatch();

  const results: Array<{
    matchId: string;
    postId?: string;
    status?: string;
    error?: string;
    /** Set when the post was written but a non-essential follow-up failed. */
    warning?: string;
  }> = [];

  for (const match of rawMatches) {
    try {
      const sport = sportOf(match.sport);
      const article = await generateArticle(match);
      const cover = await createCoverImage(article.imagePrompt, article.slug, {
        sport,
        format: match.format,
        tags: article.tags,
      });

      const status = article.confidenceScore >= AUTO_PUBLISH_THRESHOLD ? "published" : "pending";

      const post = await PostModel.create({
        matchRef: match._id,
        sport,
        title: article.title,
        slug: article.slug,
        metaDescription: article.metaDescription,
        tags: normalizeTags(article.tags),
        bodyMarkdown: article.bodyMarkdown,
        imageUrl: cover.imageUrl,
        imageAlt: cover.imageAlt ?? article.imageAlt,
        imageCredit: cover.imageCredit ?? null,
        imageCreditUrl: cover.imageCreditUrl ?? null,
        confidenceScore: article.confidenceScore,
        status,
        publishedAt: status === "published" ? new Date() : null,
      });

      await RawMatchModel.findByIdAndUpdate(match._id, { status: "processed" });

      // Cache invalidation, not part of writing the post. Letting it throw
      // into the catch below reported a post that had been created, published
      // and marked `processed` as a failed match — so a run against a blog
      // that was merely unreachable looked like a run that generated nothing.
      let warning: string | undefined;
      if (status === "published") {
        try {
          await notifyBlogToRevalidate(post.slug);
        } catch (err) {
          warning = `published, but revalidating the blog failed: ${(err as Error).message}`;
          console.warn(`${post.slug}: ${warning}`);
        }
      }

      results.push({ matchId: String(match._id), postId: String(post._id), status, warning });
    } catch (err) {
      results.push({ matchId: String(match._id), error: (err as Error).message });
    }

    await new Promise((resolve) => setTimeout(resolve, DELAY_BETWEEN_MATCHES_MS));
  }

  return NextResponse.json({ processed: results.length, results });
}
