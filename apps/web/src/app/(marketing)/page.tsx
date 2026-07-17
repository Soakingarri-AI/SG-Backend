import Link from "next/link";
import { ProductCard } from "@/components/ProductCard";
import { PRODUCTS } from "@/lib/config";

/**
 * Apex landing page. Product-led: what we build and who it's for.
 * Infrastructure choices are an implementation detail and deliberately stay off
 * the marketing surface.
 */
const PLATFORM_PILLARS = [
  {
    title: "One account, every tool",
    body: "A single SoakinGarri login carries your history and progress across all six products. No separate signups, no re-learning a new interface.",
  },
  {
    title: "Grounded, not guessed",
    body: "Our history tutor answers from a curated African source library and shows you exactly which passage each claim came from — so you can check our work.",
  },
  {
    title: "Built for local context",
    body: "Exam formats, cultural nuance, regional supply chains and pricing. The details that generic tools get wrong are the ones we start from.",
  },
  {
    title: "Respectful by design",
    body: "Cultural content passes a safety layer that rejects caricature and stereotype before it ever reaches you.",
  },
];

export default function LandingPage() {
  return (
    <main>
      {/* Hero */}
      <section className="bg-hero-radial">
        <div className="mx-auto max-w-7xl px-6 pb-20 pt-24 text-center">
          <span className="inline-flex items-center gap-2 rounded-full border border-brand-500/30 bg-brand-500/10 px-4 py-1.5 text-xs font-medium text-brand-300">
            AI built for Africa
          </span>
          <h1 className="mx-auto mt-6 max-w-4xl font-display text-5xl font-bold leading-[1.1] tracking-tight text-white md:text-6xl">
            Tools that understand{" "}
            <span className="gradient-text">where you&apos;re from.</span>
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-slate-400">
            SoakinGarri builds AI for African learners, creators, and builders —
            from exam preparation and history tutoring to cultural simulation and
            industrial planning. Six focused products, one account.
          </p>
          <div className="mt-9 flex flex-col items-center justify-center gap-4 sm:flex-row">
            <Link
              href="#products"
              className="rounded-full bg-brand-500 px-6 py-3 font-medium text-indigoblack transition hover:bg-brand-300"
            >
              Explore the products
            </Link>
            <Link
              href="/about"
              className="rounded-full border border-white/15 px-6 py-3 font-medium text-slate-200 transition hover:border-brand-500/40"
            >
              Why we build this
            </Link>
          </div>
        </div>
      </section>

      {/* Products */}
      <section id="products" className="mx-auto max-w-7xl scroll-mt-20 px-6 py-20">
        <h2 className="text-center font-display text-3xl font-bold text-white">
          What we build
        </h2>
        <p className="mx-auto mt-3 max-w-xl text-center text-slate-400">
          Each product solves one problem properly, and shares the same account
          and history with the rest.
        </p>
        <div className="mt-12 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {PRODUCTS.map((p) => (
            <ProductCard key={p.slug} product={p} />
          ))}
        </div>
      </section>

      {/* Platform */}
      <section
        id="platform"
        className="border-y border-white/5 bg-white/[0.015] scroll-mt-20"
      >
        <div className="mx-auto max-w-7xl px-6 py-20">
          <h2 className="font-display text-3xl font-bold text-white">
            How the platform works
          </h2>
          <p className="mt-3 max-w-2xl text-slate-400">
            Six products, one foundation — shared identity, shared history, and a
            common safety and quality layer underneath every answer.
          </p>
          <div className="mt-12 grid grid-cols-1 gap-6 md:grid-cols-2">
            {PLATFORM_PILLARS.map((p, i) => (
              <div
                key={p.title}
                className="rounded-2xl border border-white/10 bg-white/[0.03] p-7"
              >
                <span className="font-mono text-sm text-brand-500">
                  0{i + 1}
                </span>
                <h3 className="mt-3 font-display text-lg font-semibold text-white">
                  {p.title}
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-slate-400">
                  {p.body}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Closing CTA */}
      <section className="mx-auto max-w-7xl px-6 py-24">
        <div className="rounded-3xl border border-brand-500/20 bg-gradient-to-b from-brand-500/[0.08] to-transparent p-12 text-center card-glow">
          <h2 className="font-display text-3xl font-bold text-white">
            Start with the one you need today
          </h2>
          <p className="mx-auto mt-3 max-w-lg text-slate-300">
            Pick a product and try it. Your account works everywhere else when
            you&apos;re ready.
          </p>
          <div className="mt-8 flex flex-col items-center justify-center gap-4 sm:flex-row">
            <Link
              href="#products"
              className="rounded-full bg-brand-500 px-6 py-3 font-medium text-indigoblack transition hover:bg-brand-300"
            >
              Browse products
            </Link>
            <Link
              href="/contact"
              className="rounded-full border border-white/15 px-6 py-3 font-medium text-slate-200 transition hover:border-brand-500/40"
            >
              Talk to us
            </Link>
          </div>
        </div>
      </section>
    </main>
  );
}
