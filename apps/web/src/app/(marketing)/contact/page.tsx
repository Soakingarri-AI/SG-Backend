import type { Metadata } from "next";
import { ContactForm } from "@/components/ContactForm";
import { COMPANY } from "@/lib/company";

export const metadata: Metadata = {
  title: "Contact",
  description:
    "Get in touch with the SoakinGarri team — support, partnerships, press, and privacy enquiries.",
};

const CHANNELS = [
  {
    label: "General enquiries",
    email: COMPANY.email.general,
    note: "Partnerships, press, and anything else.",
  },
  {
    label: "Product support",
    email: COMPANY.email.support,
    note: "Something broken or behaving oddly? Start here.",
  },
  {
    label: "Privacy & data",
    email: COMPANY.email.privacy,
    note: "Data access, correction, or deletion requests.",
  },
];

export default function ContactPage() {
  return (
    <main className="mx-auto max-w-5xl px-6 py-20">
      <span className="text-xs font-semibold uppercase tracking-wider text-brand-500">
        Contact
      </span>
      <h1 className="mt-3 font-display text-4xl font-bold text-white md:text-5xl">
        Talk to us
      </h1>
      <p className="mt-4 max-w-2xl text-lg text-slate-400">
        Questions, feedback, partnership ideas, or a bug that&apos;s driving you
        mad — send it over and a person will read it.
      </p>

      <div className="mt-12 grid grid-cols-1 gap-10 lg:grid-cols-[1.3fr_1fr]">
        <ContactForm />

        <aside className="space-y-4">
          {CHANNELS.map((c) => (
            <div
              key={c.email}
              className="rounded-2xl border border-white/10 bg-white/[0.03] p-6"
            >
              <p className="font-display text-sm font-semibold text-white">
                {c.label}
              </p>
              <a
                href={`mailto:${c.email}`}
                className="mt-1 block text-sm text-brand-300 hover:underline"
              >
                {c.email}
              </a>
              <p className="mt-2 text-xs leading-relaxed text-slate-500">{c.note}</p>
            </div>
          ))}

          <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6">
            <p className="font-display text-sm font-semibold text-white">
              Registered office
            </p>
            <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-slate-400">
              {COMPANY.legalName}
              {"\n"}
              {COMPANY.address}
            </p>
          </div>
        </aside>
      </div>
    </main>
  );
}
