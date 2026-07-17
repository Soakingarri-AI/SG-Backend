import type { Metadata } from "next";
import Link from "next/link";
import { COMPANY } from "@/lib/company";

export const metadata: Metadata = {
  title: "About",
  description:
    "Why SoakinGarri builds AI for African learners, creators, and builders — and the principles behind how we build it.",
};

const PRINCIPLES = [
  {
    title: "Context is the product",
    body: "A tutor that doesn't know the WAEC syllabus, or a planner that doesn't know what a generator costs in Lagos, isn't useful here. We start from local reality rather than translating someone else's.",
  },
  {
    title: "Show your sources",
    body: "When our history tutor makes a claim, it shows the passage it came from. Users should be able to check us, disagree with us, and go read the original.",
  },
  {
    title: "Dignity is non-negotiable",
    body: "African cultures are specific, plural, and living. Our cultural tools carry a safety layer that rejects caricature, mock accents, and flattening stereotypes.",
  },
  {
    title: "Be honest about limits",
    body: "AI estimates are estimates. Where our output touches the physical world — machining tolerances, factory budgets — we say plainly that it needs human validation before anyone spends money or cuts metal.",
  },
];

export default function AboutPage() {
  return (
    <main className="mx-auto max-w-4xl px-6 py-20">
      <span className="text-xs font-semibold uppercase tracking-wider text-brand-500">
        About us
      </span>
      <h1 className="mt-3 font-display text-4xl font-bold leading-tight text-white md:text-5xl">
        We build AI that starts from African context.
      </h1>

      <div className="mt-8 space-y-6 text-lg leading-relaxed text-slate-300">
        <p>
          Most AI tools are built somewhere else and adapted here afterwards. The
          gap shows up in small, costly ways: a study assistant that has never
          seen a JAMB past question, a history answer that cites nothing you can
          verify, a cost model built for a supply chain that doesn&apos;t exist
          on this continent.
        </p>
        <p>
          {COMPANY.name} exists to close that gap. We build focused AI products
          for African learners, creators, and builders — each one solving a real
          problem properly, and all of them sharing a single account, a single
          history, and a common standard for accuracy and respect.
        </p>
      </div>

      {/* What we build */}
      <section className="mt-16">
        <h2 className="font-display text-2xl font-bold text-white">What we build</h2>
        <p className="mt-3 leading-relaxed text-slate-400">
          Six products across education, culture, and industry: an African-history
          tutor grounded in cited sources; an exam practice platform covering
          WAEC, JAMB, NECO and Common Entrance; a cultural simulation sandbox; a
          localized humor generator; a natural-language tool for parametric
          mechanical parts; and a factory setup planner. They look different on
          the surface because they serve different people — underneath, they run
          on the same foundation.
        </p>
      </section>

      {/* Principles */}
      <section className="mt-16">
        <h2 className="font-display text-2xl font-bold text-white">
          How we build it
        </h2>
        <div className="mt-8 grid grid-cols-1 gap-6 sm:grid-cols-2">
          {PRINCIPLES.map((p) => (
            <div
              key={p.title}
              className="rounded-2xl border border-white/10 bg-white/[0.03] p-6"
            >
              <h3 className="font-display text-base font-semibold text-brand-300">
                {p.title}
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-slate-400">
                {p.body}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* CTA */}
      <section className="mt-16 rounded-2xl border border-white/10 bg-white/[0.03] p-8">
        <h2 className="font-display text-xl font-semibold text-white">
          Work with us
        </h2>
        <p className="mt-2 text-slate-400">
          Partnerships, press, schools, or feedback on a product — we read
          everything.
        </p>
        <Link
          href="/contact"
          className="mt-5 inline-flex rounded-full bg-brand-500 px-5 py-2.5 text-sm font-medium text-indigoblack transition hover:bg-brand-300"
        >
          Get in touch
        </Link>
      </section>
    </main>
  );
}
