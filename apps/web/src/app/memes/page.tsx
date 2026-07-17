"use client";

import { useState } from "react";
import { ProductShell } from "@/components/ProductShell";
import { api } from "@/lib/api";

interface Meme {
  id: string;
  expectation: string;
  reality: string;
  caption: string;
}

export default function MemesPage() {
  const [prompt, setPrompt] = useState("");
  const [meme, setMeme] = useState<Meme | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  async function generate() {
    setLoading(true);
    const res = await api<Meme>("/memes", {
      method: "POST",
      json: { prompt, style: "nigerian_student" },
    });
    setMeme(res);
    setLoading(false);
  }

  function copy() {
    if (!meme) return;
    navigator.clipboard.writeText(
      `Expectation: ${meme.expectation}\nReality: ${meme.reality}\n— ${meme.caption}`,
    );
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <ProductShell
      title="Meme Generator"
      subtitle="Expectation vs. reality — Nigerian student edition."
    >
      <div className="flex gap-3">
        <input
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="e.g. 'reading for JAMB the night before'"
          className="flex-1 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-white placeholder:text-slate-500 focus:border-brand-500/50 focus:outline-none"
        />
        <button
          onClick={generate}
          disabled={!prompt.trim() || loading}
          className="rounded-xl bg-brand-500 px-6 py-3 font-medium text-indigoblack hover:bg-brand-300 disabled:opacity-50"
        >
          {loading ? "Cooking…" : "Generate"}
        </button>
      </div>

      {meme && (
        <div className="mt-8 overflow-hidden rounded-2xl border border-white/10 card-glow">
          <div className="grid grid-cols-2 divide-x divide-white/10">
            <div className="bg-emerald-500/[0.06] p-6">
              <p className="text-xs font-semibold uppercase tracking-wide text-emerald-400">
                Expectation
              </p>
              <p className="mt-2 text-lg text-white">{meme.expectation}</p>
            </div>
            <div className="bg-rose-500/[0.06] p-6">
              <p className="text-xs font-semibold uppercase tracking-wide text-rose-400">
                Reality
              </p>
              <p className="mt-2 text-lg text-white">{meme.reality}</p>
            </div>
          </div>
          <div className="flex items-center justify-between bg-white/[0.03] p-5">
            <p className="font-display text-lg text-brand-300">“{meme.caption}”</p>
            <button
              onClick={copy}
              className="rounded-lg border border-white/15 px-4 py-2 text-sm text-slate-200 hover:border-brand-500/40"
            >
              {copied ? "Copied ✓" : "Copy"}
            </button>
          </div>
        </div>
      )}
    </ProductShell>
  );
}
