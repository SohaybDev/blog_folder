import { GoogleGenAI, Modality, Type } from "@google/genai";
import {
  hasResults,
  sportOf,
  type GeneratedArticle,
  type RawMatch,
  type Sport,
} from "@cricket-blog/types";

const TEXT_MODEL = "gemini-3.5-flash";
// Image model must be a generateContent-style image model, not an Imagen
// `predict` model — Imagen is not available on newer API keys.
const IMAGE_MODEL = "gemini-3.1-flash-image";

function getClient() {
  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) throw new Error("GEMINI_API_KEY is not set");
  return new GoogleGenAI({ apiKey });
}

const ARTICLE_SCHEMA = {
  type: Type.OBJECT,
  properties: {
    title: { type: Type.STRING },
    slug: { type: Type.STRING },
    metaDescription: { type: Type.STRING },
    tags: { type: Type.ARRAY, items: { type: Type.STRING } },
    bodyMarkdown: { type: Type.STRING },
    imageAlt: { type: Type.STRING },
    imagePrompt: { type: Type.STRING },
    confidenceScore: { type: Type.NUMBER },
  },
  required: [
    "title",
    "slug",
    "metaDescription",
    "tags",
    "bodyMarkdown",
    "imageAlt",
    "imagePrompt",
    "confidenceScore",
  ],
};

/**
 * Per-sport wording for the shared prompt skeleton. Without this the model
 * writes cricket idiom about a motor race ("both sides", "the format") — the
 * facts stay correct but the prose reads as though nobody watched the sport.
 */
const SPORT_BRIEFS: Record<
  Sport,
  { role: string; eventNoun: string; previewFocus: string; reportFocus: string; tagHint: string }
> = {
  cricket: {
    role: "a cricket journalist",
    eventNoun: "match",
    previewFocus:
      "the two teams, the venue's typical conditions, and what will decide the contest — plus where the series stands going in, but only if the facts carry a series scoreline",
    reportFocus:
      "how the innings totals shaped the result (two innings in a limited-overs match, up to four in a Test), what the margin says about each side, and what it means for the tournament — naming individual players only if the facts below name them, since some sources supply team totals without a per-player scorecard",
    tagHint: "team names, the format, the tournament name",
  },
  motorsport: {
    role: "a Formula 1 journalist",
    eventNoun: "race",
    previewFocus:
      "the championship picture, this circuit's specific demands (overtaking difficulty, tyre wear, weather risk), and the session schedule leading into the race",
    reportFocus:
      "the winning drive, the decisive moments in the finishing order, notable retirements, constructor implications, and how the championship shifts",
    tagHint: "the Grand Prix name, driver names, constructor names, the season",
  },
};

function buildPrompt(match: RawMatch): string {
  const sport = sportOf(match.sport);
  const brief = SPORT_BRIEFS[sport];

  // No results of any kind means the event hasn't been contested yet (a fixture,
  // not a result) — write a preview rather than inventing an outcome.
  const isPreview = !hasResults(match);

  const factsBlock = JSON.stringify(
    {
      eventTitle: match.matchTitle,
      competition: match.competition,
      teams: match.teams,
      venue: match.venue,
      date: match.date,
      format: match.format,
      scorecard: match.scorecard,
      playerPerformances: match.playerPerformances,
      standings: match.standings,
    },
    (key, value) =>
      // Provenance-only bookkeeping that must never surface in the prose.
      key === "attribution" || key === "referenceUrl" ? undefined : value,
    2
  );

  const shared = `Requirements:
- title: SEO-friendly, under 70 characters
- slug: lowercase, hyphenated, derived from the title
- metaDescription: under 160 characters
- tags: 3-6 relevant tags (${brief.tagHint})
- imageAlt: descriptive alt text for a cover image (accessibility + SEO)
- imagePrompt: a prompt to hand to an image generator for a cover image depicting this
  ${brief.eventNoun}'s theme (no text/logos/scoreboards in the image, editorial/photographic style)`;

  if (isPreview) {
    return `You are ${brief.role} writing an ORIGINAL preview article for an UPCOMING
${brief.eventNoun}. You are given structured facts (not prose from any source, and no
result exists yet). Write a genuinely new ~400-word preview — never invent a score,
winner, finishing position, or any event that has not happened. Focus on
${brief.previewFocus}.

Facts (JSON):
${factsBlock}

${shared}
- bodyMarkdown: ~400 words, Markdown formatted, preview-style (no invented outcome).
  The title must read as a preview, not as a result.
- confidenceScore: 0.0-1.0, your own estimate of how factually grounded this preview is
  given the input data (low if the data was sparse)`;
  }

  return `You are ${brief.role} writing an ORIGINAL analysis article about a ${brief.eventNoun}
that has been contested. You are given structured facts (not prose from any source).
Write a genuinely new ~600-word article — never copy or lightly reword any external
text, because you were not given any. Every name, position, time and number you cite
must come from the facts below; do not add any you were not given.
Focus on ${brief.reportFocus}.

Facts (JSON):
${factsBlock}

${shared}
- bodyMarkdown: ~600 words, Markdown formatted, headings where useful, analysis-driven
  (not a position-by-position restatement of the results table)
- confidenceScore: 0.0-1.0, your own estimate of how factually grounded and complete this
  article is given the input data (low if the data was sparse or ambiguous)`;
}

/**
 * Coerce whatever scale the model answered on into 0-1.
 *
 * Percentages (85) and 0-10 scores (9.2) both appear in practice. Anything
 * unparseable becomes 0, which routes the post to manual review rather than
 * auto-publishing it on a score we don't trust.
 */
export function normalizeConfidence(raw: unknown): number {
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0) return 0;
  if (value <= 1) return value;
  if (value <= 10) return value / 10;
  if (value <= 100) return value / 100;
  return 1;
}

const MAX_ATTEMPTS = 4;

// 429 (rate limited) and 503 (model overloaded) are routinely transient on the
// free tier, so retry those with backoff instead of dropping the match.
//
// A SyntaxError from JSON.parse belongs here too: despite the response schema,
// the model occasionally degenerates into repetition and returns a megabyte of
// unterminated JSON. That's a bad roll of the dice, not a bad prompt, and a
// retry produces valid output.
function isTransient(err: unknown): boolean {
  if (err instanceof SyntaxError) return true;
  const message = (err as Error)?.message ?? "";
  return message.includes("429") || message.includes("503");
}

async function withRetry<T>(label: string, fn: () => Promise<T>): Promise<T> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastError = err;
      if (!isTransient(err) || attempt === MAX_ATTEMPTS) break;
      const delayMs = 5000 * 2 ** (attempt - 1);
      console.warn(`${label} attempt ${attempt} failed, retrying in ${delayMs / 1000}s`);
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }
  throw lastError;
}

export async function generateArticle(match: RawMatch): Promise<GeneratedArticle> {
  const client = getClient();
  return withRetry("generateArticle", async () => {
    const response = await client.models.generateContent({
      model: TEXT_MODEL,
      contents: buildPrompt(match),
      config: {
        responseMimeType: "application/json",
        responseSchema: ARTICLE_SCHEMA,
      },
    });

    const text = response.text;
    if (!text) throw new Error("Gemini returned no text for article generation");
    const article = JSON.parse(text) as GeneratedArticle;

    // Gemini emits literal "\n" sequences inside the JSON string rather than
    // real newlines, which leaves the Markdown unparseable (headings and
    // paragraph breaks render as raw text).
    article.bodyMarkdown = article.bodyMarkdown.replace(/\\n/g, "\n");

    // The prompt asks for 0.0-1.0 but the model sometimes answers on a 0-10 or
    // percentage scale, and the Post schema rejects anything above 1 — which
    // threw away an otherwise good article. Rescale the obvious cases, clamp the
    // rest, since the score only gates auto-publish and is not itself content.
    article.confidenceScore = normalizeConfidence(article.confidenceScore);

    return article;
  });
}

export async function generateCoverImage(imagePrompt: string): Promise<Buffer> {
  const client = getClient();
  const response = await client.models.generateContent({
    model: IMAGE_MODEL,
    contents: imagePrompt,
    config: { responseModalities: [Modality.IMAGE] },
  });

  const parts = response.candidates?.[0]?.content?.parts ?? [];
  const imageData = parts.find((part) => part.inlineData?.data)?.inlineData?.data;
  if (!imageData) throw new Error("Gemini returned no image bytes");
  return Buffer.from(imageData, "base64");
}
