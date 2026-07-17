import Link from "next/link";
import { Logo } from "./Logo";
import { IS_STATIC_DEMO, ROOT_DOMAIN } from "@/lib/config";

export function ProductShell({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-hero-radial">
      <header className="border-b border-white/5 bg-indigoblack/70 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <Logo size={34} />
          <Link
            href={IS_STATIC_DEMO ? "/" : `https://${ROOT_DOMAIN}`}
            className="text-sm text-slate-400 hover:text-brand-300"
          >
            ← All products
          </Link>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-6 py-10">
        <h1 className="font-display text-3xl font-bold text-white">{title}</h1>
        <p className="mt-2 text-slate-400">{subtitle}</p>
        <div className="mt-8">{children}</div>
      </main>
    </div>
  );
}
