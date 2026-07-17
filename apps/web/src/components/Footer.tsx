import Link from "next/link";
import { COMPANY } from "@/lib/company";
import { PRODUCTS, productUrl } from "@/lib/config";

const COMPANY_LINKS = [
  { href: "/about", label: "About" },
  { href: "/contact", label: "Contact" },
];

const LEGAL_LINKS = [
  { href: "/privacy", label: "Privacy Policy" },
  { href: "/terms", label: "Terms & Conditions" },
];

export function Footer() {
  return (
    <footer className="border-t border-white/5 bg-black/20">
      <div className="mx-auto max-w-7xl px-6 py-14">
        <div className="grid grid-cols-2 gap-10 md:grid-cols-4">
          <div className="col-span-2 md:col-span-1">
            <p className="font-display text-lg font-semibold text-white">
              SoakinGarri<span className="gradient-text"> AI</span>
            </p>
            <p className="mt-3 max-w-xs text-sm leading-relaxed text-slate-400">
              AI products built for African learners, creators, and builders.
            </p>
            <div className="mt-4 flex gap-4 text-sm text-slate-400">
              <a href={COMPANY.social.x} className="hover:text-brand-300">
                X
              </a>
              <a href={COMPANY.social.linkedin} className="hover:text-brand-300">
                LinkedIn
              </a>
            </div>
          </div>

          <FooterCol title="Products">
            {PRODUCTS.map((p) => (
              <FooterLink key={p.slug} href={productUrl(p.subdomain)}>
                {p.name}
              </FooterLink>
            ))}
          </FooterCol>

          <FooterCol title="Company">
            {COMPANY_LINKS.map((l) => (
              <FooterLink key={l.href} href={l.href}>
                {l.label}
              </FooterLink>
            ))}
          </FooterCol>

          <FooterCol title="Legal">
            {LEGAL_LINKS.map((l) => (
              <FooterLink key={l.href} href={l.href}>
                {l.label}
              </FooterLink>
            ))}
            <FooterLink href={`mailto:${COMPANY.email.support}`}>Support</FooterLink>
          </FooterCol>
        </div>

        <div className="mt-12 flex flex-col gap-2 border-t border-white/5 pt-6 text-sm text-slate-500 md:flex-row md:items-center md:justify-between">
          <p>
            © {new Date().getFullYear()} {COMPANY.legalName}. All rights reserved.
          </p>
          <p>{COMPANY.domain}</p>
        </div>
      </div>
    </footer>
  );
}

function FooterCol({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
        {title}
      </p>
      <ul className="mt-4 space-y-2.5">{children}</ul>
    </div>
  );
}

function FooterLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <li>
      <Link href={href} className="text-sm text-slate-400 transition hover:text-brand-300">
        {children}
      </Link>
    </li>
  );
}
