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

export function productUrl(subdomain: string): string {
  if (process.env.NODE_ENV === "development") {
    return `http://${subdomain}.localhost:3000`;
  }
  return `https://${subdomain}.${ROOT_DOMAIN}`;
}
