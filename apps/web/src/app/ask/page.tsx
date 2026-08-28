"use client";

import { useState } from "react";
import { ProductShell } from "@/components/ProductShell";
import { api, ApiError } from "@/lib/api";

type LearningMode = "beginner" | "normal" | "advanced";

interface AskSource {
  title: string;
  source_url: string | null;
  snippet: string;
  category: string;
}
interface AskResponse {
  session_id: string;
  message_id: string;
  answer: string;
  learning_mode: LearningMode;
  sources: AskSource[];
}

const MODES: LearningMode[] = ["beginner", "normal", "advanced"];

export default function AskPage() {
  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState<LearningMode>("normal");
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
        json: {
          prompt,
          learning_mode: mode,
          session_id: result?.session_id ?? null,
        },
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
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Ask about the Mali Empire, Queen Amina, the Benin Bronzes…"
          className="flex-1 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-white placeholder:text-slate-500 focus:border-brand-500/50 focus:outline-none"
        />
        <button
          disabled={loading || !prompt.trim()}
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
            {result.sources.length === 0 && (
              <p className="rounded-xl border border-white/10 bg-white/[0.02] p-4 text-xs text-slate-400">
                The source library is still loading — this answer draws on the
                tutor&apos;s general knowledge and citations will appear here soon.
              </p>
            )}
            {result.sources.map((s, i) => (
              <div
                key={i}
                className="rounded-xl border border-white/10 bg-white/[0.02] p-4"
              >
                <p className="text-sm font-medium text-white">
                  [{i + 1}]{" "}
                  {s.source_url ? (
                    <a
                      href={s.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="underline decoration-brand-500/50 hover:text-brand-300"
                    >
                      {s.title}
                    </a>
                  ) : (
                    s.title
                  )}
                </p>
                <p className="mt-1 line-clamp-3 text-xs text-slate-400">
                  {s.snippet}
                </p>
                <p className="mt-2 text-[10px] uppercase tracking-wide text-slate-500">
                  {s.category.replaceAll("_", " ")}
                </p>
              </div>
            ))}
          </aside>
        </div>
      )}
    </ProductShell>
  );
}
