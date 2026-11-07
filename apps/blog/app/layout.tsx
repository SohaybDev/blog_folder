import type { Metadata } from "next";
import { Playfair_Display, Source_Serif_4, Inter } from "next/font/google";
import { SiteHeader } from "./components/site-header";
import { SiteFooter } from "./components/site-footer";
import { getSportCounts, getTagIndex } from "@/lib/posts";
import { SITE_NAME, SITE_TAGLINE, siteUrl } from "@/lib/site";
import "./globals.css";

const playfair = Playfair_Display({
  variable: "--font-playfair",
  subsets: ["latin"],
  weight: ["400", "700", "900"],
});

const sourceSerif = Source_Serif_4({
  variable: "--font-source-serif",
  subsets: ["latin"],
});

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl()),
  title: {
    default: `${SITE_NAME} — Formula 1 and cricket analysis`,
    template: `%s | ${SITE_NAME}`,
  },
  description: SITE_TAGLINE,
  // Google Search Console ownership (HTML-tag method) for blog-folder-blog.vercel.app.
  // Keep it: removing the tag un-verifies the property.
  verification: {
    google: "qx1ucNOPlSOqj2IZOA3yqCm8x0UWAVgKpSaRewW_AT8",
  },
};

export default async function RootLayout({ children }: LayoutProps<"/">) {
  // Both the masthead and footer hide sports with nothing published, so the
  // counts are fetched once here rather than in each component.
  const [tags, counts] = await Promise.all([getTagIndex(), getSportCounts()]);

  return (
    <html
      lang="en"
      className={`${playfair.variable} ${sourceSerif.variable} ${inter.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col">
        <SiteHeader counts={counts} />
        <div className="flex-1">{children}</div>
        <SiteFooter tags={tags} counts={counts} />
      </body>
    </html>
  );
}
