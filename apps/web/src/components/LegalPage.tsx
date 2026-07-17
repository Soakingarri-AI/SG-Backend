import { COMPANY } from "@/lib/company";

export function LegalPage({
  title,
  intro,
  children,
}: {
  title: string;
  intro: string;
  children: React.ReactNode;
}) {
  return (
    <main className="mx-auto max-w-3xl px-6 py-20">
      <h1 className="font-display text-4xl font-bold text-white md:text-5xl">
        {title}
      </h1>
      <p className="mt-3 text-sm text-slate-500">
        Last updated: {COMPANY.legalLastUpdated}
      </p>
      <p className="mt-6 text-lg leading-relaxed text-slate-300">{intro}</p>
      <div className="mt-10 space-y-10">{children}</div>
    </main>
  );
}

export function Section({
  n,
  title,
  children,
}: {
  n: number;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <h2 className="font-display text-xl font-semibold text-white">
        <span className="mr-2 font-mono text-sm text-brand-500">{n}.</span>
        {title}
      </h2>
      <div className="mt-3 space-y-3 leading-relaxed text-slate-400">{children}</div>
    </section>
  );
}

export function Bullets({ items }: { items: string[] }) {
  return (
    <ul className="list-disc space-y-2 pl-5 marker:text-brand-500">
      {items.map((t, i) => (
        <li key={i}>{t}</li>
      ))}
    </ul>
  );
}
