"use client";

import { useState } from "react";
import { ProductShell } from "@/components/ProductShell";
import { api, ApiError } from "@/lib/api";

type Mode = "beginner" | "normal" | "advanced";

interface Citation {
  document_title: string;
  source_path: string;
  snippet: string;
  score: number;
}
interface AskResponse {
  answer: string;
  citations: Citation[];
  session_id: string;
  mode: Mode;
}

const MODES: Mode[] = ["beginner", "normal", "advanced"];

export default function AskPage() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<Mode>("normal");
  const [result, setResult] = useState<AskResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const res = await api<AskResponse>("/ask", {
        method: "POST",
        json: { query, mode, session_id: result?.session_id ?? null },
      });
      setResult(res);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  return (
    <ProductShell
      title="Ask SoakinGarri"
      subtitle="African-history AI tutor grounded in cited sources."
    >
      {/* Learning mode toggle */}
      <div className="mb-5 inline-flex rounded-full border border-white/10 bg-white/[0.03] p-1">
        {MODES.map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`rounded-full px-4 py-1.5 text-sm capitalize transition ${
              mode === m
                ? "bg-brand-500 text-indigoblack"
                : "text-slate-300 hover:text-brand-300"
            }`}
          >
            {m}
          </button>
        ))}
      </div>

      <form onSubmit={submit} className="flex gap-3">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Ask about the Mali Empire, Queen Amina, the Benin Bronzes…"
          className="flex-1 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-white placeholder:text-slate-500 focus:border-brand-500/50 focus:outline-none"
        />
        <button
          disabled={loading || !query.trim()}
          className="rounded-xl bg-brand-500 px-6 py-3 font-medium text-indigoblack transition hover:bg-brand-300 disabled:opacity-50"
        >
          {loading ? "Thinking…" : "Ask"}
        </button>
      </form>

      {error && <p className="mt-4 text-sm text-rose-400">{error}</p>}

      {result && (
        <div className="mt-8 grid gap-6 lg:grid-cols-[2fr_1fr]">
          <article className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 leading-relaxed text-slate-200 whitespace-pre-wrap">
            {result.answer}
          </article>
          <aside className="space-y-3">
            <h3 className="text-sm font-semibold uppercase tracking-wide text-brand-300">
              Sources
            </h3>
            {result.citations.map((c, i) => (
              <div
                key={i}
                className="rounded-xl border border-white/10 bg-white/[0.02] p-4"
              >
                <p className="text-sm font-medium text-white">
                  [{i + 1}] {c.document_title}
                </p>
                <p className="mt-1 line-clamp-3 text-xs text-slate-400">
                  {c.snippet}
                </p>
                <p className="mt-2 text-[10px] text-slate-500">
                  relevance {(c.score * 100).toFixed(0)}%
                </p>
              </div>
            ))}
          </aside>
        </div>
      )}
    </ProductShell>
  );
}
