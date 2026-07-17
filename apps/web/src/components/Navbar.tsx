"use client";

import Link from "next/link";
import { useState } from "react";
import { Logo } from "./Logo";

const LINKS = [
  { href: "/#products", label: "Products" },
  { href: "/#platform", label: "Platform" },
  { href: "/about", label: "About" },
  { href: "/contact", label: "Contact" },
];

export function Navbar() {
  const [open, setOpen] = useState(false);

  return (
    <header className="sticky top-0 z-40 border-b border-white/5 bg-indigoblack/80 backdrop-blur">
      <nav className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
        <Logo />

        <div className="hidden items-center gap-8 text-sm text-slate-300 md:flex">
          {LINKS.map((l) => (
            <Link key={l.href} href={l.href} className="transition hover:text-brand-300">
              {l.label}
            </Link>
          ))}
        </div>

        <div className="flex items-center gap-3">
          <Link
            href="/#products"
            className="hidden rounded-full bg-brand-500 px-4 py-2 text-sm font-medium text-indigoblack transition hover:bg-brand-300 sm:inline-flex"
          >
            Get started
          </Link>
          <button
            onClick={() => setOpen((o) => !o)}
            aria-label="Toggle menu"
            aria-expanded={open}
            className="text-slate-300 md:hidden"
          >
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
              <path
                d={open ? "M6 6l12 12M18 6L6 18" : "M4 7h16M4 12h16M4 17h16"}
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>
      </nav>

      {open && (
        <div className="border-t border-white/5 px-6 py-4 md:hidden">
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              onClick={() => setOpen(false)}
              className="block py-2 text-sm text-slate-300 hover:text-brand-300"
            >
              {l.label}
            </Link>
          ))}
        </div>
      )}
    </header>
  );
}
