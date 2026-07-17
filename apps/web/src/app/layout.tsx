import type { Metadata } from "next";
import { Inter, Sora } from "next/font/google";
import "./globals.css";
import { COMPANY } from "@/lib/company";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

// Display face for headings — geometric, confident, pairs cleanly with Inter.
const sora = Sora({
  subsets: ["latin"],
  variable: "--font-display",
  weight: ["400", "600", "700", "800"],
  display: "swap",
});

export const metadata: Metadata = {
  metadataBase: new URL(`https://${COMPANY.domain}`),
  title: {
    default: `${COMPANY.name} — AI built for Africa`,
    template: `%s · ${COMPANY.name}`,
  },
  description:
    "SoakinGarri builds AI products for African learners, creators, and builders — from history tutoring and exam preparation to cultural simulation and industrial planning.",
  keywords: [
    "African AI",
    "exam preparation",
    "WAEC",
    "JAMB",
    "African history",
    "AI tutor",
  ],
  openGraph: {
    title: `${COMPANY.name} — AI built for Africa`,
    description:
      "AI products for African learners, creators, and builders. One account, six tools.",
    url: `https://${COMPANY.domain}`,
    siteName: COMPANY.name,
    images: ["/logo.png"],
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: `${COMPANY.name} — AI built for Africa`,
    images: ["/logo.png"],
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${inter.variable} ${sora.variable}`}>
      <body>{children}</body>
    </html>
  );
}
