export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export const ROOT_DOMAIN =
  process.env.NEXT_PUBLIC_ROOT_DOMAIN ?? "soakingarri.com";

export const API_V1 = `${API_URL}/api/v1`;

export interface Product {
  slug: string;
  name: string;
  subdomain: string;
  tagline: string;
  description: string;
  accent: string;
}

export const PRODUCTS: Product[] = [
  {
    slug: "ask",
    name: "Ask SoakinGarri",
    subdomain: "ask",
    tagline: "African-history AI tutor with cited sources",
    description:
      "RAG-grounded teaching assistant. Toggle Beginner / Normal / Advanced learning modes.",
    accent: "from-amber-400 to-orange-600",
  },
  {
    slug: "examflow",
    name: "ExamFlow",
    subdomain: "examflow",
    tagline: "WAEC · JAMB · NECO · Common Entrance practice",
    description:
      "Mixer-generated custom tests, live timer, and step-by-step AI corrections.",
    accent: "from-emerald-400 to-teal-600",
  },
  {
    slug: "afrosimulator",
    name: "AfroSimulator",
    subdomain: "afrosimulator",
    tagline: "Yoruba · Igbo · Hausa cultural multi-agent sandbox",
    description:
      "Watch culturally-safe AI personas converse, exchange proverbs, and summarize.",
    accent: "from-fuchsia-400 to-purple-600",
  },
  {
    slug: "memes",
    name: "Meme Generator",
    subdomain: "memes",
    tagline: "Expectation vs. reality, Naija student edition",
    description:
      "Strict JSON humor engine. One tap to copy or save your meme card.",
    accent: "from-pink-400 to-rose-600",
  },
  {
    slug: "infiniteparts",
    name: "InfiniteParts",
    subdomain: "infiniteparts",
    tagline: "Natural-language parametric 3D parts",
    description:
      "Prompt → validated dimensions → live React-Three-Fiber preview you can tune.",
    accent: "from-sky-400 to-blue-600",
  },
  {
    slug: "factorizer",
    name: "Factorizer",
    subdomain: "factorizer",
    tagline: "Industrial factory setup wizard",
    description:
      "A guided wizard that returns a 15-section factory plan with costs and machines.",
    accent: "from-lime-400 to-green-600",
  },
];

/**
 * True in the GitHub Pages static demo. There are no subdomains on a single
 * static host, so products are reached by path instead (/ask, /examflow, …).
 */
export const IS_STATIC_DEMO = process.env.NEXT_PUBLIC_STATIC_DEMO === "true";

/** Repo-name base path, only set in the Pages build (e.g. "/SG-Backend"). */
export const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

/**
 * Prefix a public/ asset with the base path. next/image with `unoptimized`
 * does not auto-apply basePath, so static/img srcs must be built with this.
 */
export function asset(path: string): string {
  return `${IS_STATIC_DEMO ? BASE_PATH : ""}${path}`;
}

export function productUrl(subdomain: string): string {
  // Static demo: internal path. next/link auto-prepends basePath (/SG-Backend).
  if (IS_STATIC_DEMO) {
    return `/${subdomain}`;
  }
  if (process.env.NODE_ENV === "development") {
    return `http://${subdomain}.localhost:3000`;
  }
  return `https://${subdomain}.${ROOT_DOMAIN}`;
}
