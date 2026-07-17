import Link from "next/link";
import { Product, productUrl } from "@/lib/config";

export function ProductCard({ product }: { product: Product }) {
  return (
    <Link
      href={productUrl(product.subdomain)}
      className="group relative overflow-hidden rounded-2xl border border-white/10 bg-white/[0.03] p-6 transition hover:border-brand-500/40 hover:bg-white/[0.06]"
    >
      <div
        className={`mb-4 inline-flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br ${product.accent} text-lg font-bold text-white`}
      >
        {product.name.charAt(0)}
      </div>
      <h3 className="text-lg font-semibold text-white">{product.name}</h3>
      <p className="mt-1 text-sm font-medium text-brand-300">{product.tagline}</p>
      <p className="mt-3 text-sm leading-relaxed text-slate-400">
        {product.description}
      </p>
      <span className="mt-5 inline-flex items-center gap-1 text-sm font-medium text-slate-300 group-hover:text-brand-300">
        {product.subdomain}.soakingarri.com
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" className="transition group-hover:translate-x-1">
          <path d="M5 12h14M13 6l6 6-6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
    </Link>
  );
}
