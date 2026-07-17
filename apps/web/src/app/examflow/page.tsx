"use client";

import { useEffect, useMemo, useState } from "react";
import { ProductShell } from "@/components/ProductShell";
import { api } from "@/lib/api";

interface Question {
  position: number;
  id: string;
  stem: string;
  options: { key: string; text: string }[];
}
interface SessionResp {
  session_id: string;
  duration_seconds: number;
  questions: Question[];
}
interface Correction {
  position: number;
  stem: string;
  your_answer: string | null;
  correct_answer: string;
  explanation: string | null;
}
interface Result {
  score: number;
  correct: number;
  total: number;
  corrections: Correction[];
}

export default function ExamFlowPage() {
  const [session, setSession] = useState<SessionResp | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [current, setCurrent] = useState(0);
  const [remaining, setRemaining] = useState(0);
  const [result, setResult] = useState<Result | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!session || result) return;
    const t = setInterval(() => setRemaining((r) => Math.max(0, r - 1)), 1000);
    return () => clearInterval(t);
  }, [session, result]);

  useEffect(() => {
    if (session && remaining === 0 && !result) submit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [remaining]);

  async function start() {
    setLoading(true);
    const res = await api<SessionResp>("/examflow/sessions", {
      method: "POST",
      json: { board: "JAMB", subject: "Mathematics", num_questions: 20 },
    });
    setSession(res);
    setRemaining(res.duration_seconds);
    setLoading(false);
  }

  async function submit() {
    if (!session) return;
    const res = await api<Result>(`/examflow/sessions/${session.session_id}/submit`, {
      method: "POST",
      json: { answers },
    });
    setResult(res);
  }

  const clock = useMemo(() => {
    const m = Math.floor(remaining / 60).toString().padStart(2, "0");
    const s = (remaining % 60).toString().padStart(2, "0");
    return `${m}:${s}`;
  }, [remaining]);

  if (!session) {
    return (
      <ProductShell title="ExamFlow" subtitle="WAEC · JAMB · NECO · Common Entrance practice.">
        <button
          onClick={start}
          disabled={loading}
          className="rounded-xl bg-brand-500 px-6 py-3 font-medium text-indigoblack hover:bg-brand-300 disabled:opacity-50"
        >
          {loading ? "Generating…" : "Start JAMB Mathematics mock"}
        </button>
      </ProductShell>
    );
  }

  if (result) {
    return (
      <ProductShell title="ExamFlow — Results" subtitle={`Score: ${result.score}%`}>
        <p className="mb-6 text-slate-300">
          {result.correct} / {result.total} correct
        </p>
        <div className="space-y-4">
          {result.corrections.map((c) => (
            <div key={c.position} className="rounded-xl border border-rose-500/20 bg-rose-500/[0.04] p-5">
              <p className="font-medium text-white">
                Q{c.position + 1}. {c.stem}
              </p>
              <p className="mt-2 text-sm text-slate-300">
                Your answer: <span className="text-rose-400">{c.your_answer ?? "—"}</span> ·
                Correct: <span className="text-emerald-400">{c.correct_answer}</span>
              </p>
              {c.explanation && (
                <p className="mt-2 text-sm text-slate-400">{c.explanation}</p>
              )}
            </div>
          ))}
        </div>
      </ProductShell>
    );
  }

  const q = session.questions[current];
  return (
    <ProductShell title="ExamFlow" subtitle="Answer every question before the timer ends.">
      <div className="mb-4 flex items-center justify-between">
        <span className="rounded-full bg-white/[0.05] px-4 py-1.5 font-mono text-brand-300">
          ⏱ {clock}
        </span>
        <button
          onClick={submit}
          className="rounded-lg border border-brand-500/40 px-4 py-1.5 text-sm text-brand-300 hover:bg-brand-500/10"
        >
          Submit exam
        </button>
      </div>

      <div className="grid gap-6 md:grid-cols-[1fr_200px]">
        <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6">
          <p className="text-sm text-slate-500">
            Question {current + 1} of {session.questions.length}
          </p>
          <p className="mt-2 text-lg text-white">{q.stem}</p>
          <div className="mt-5 space-y-3">
            {q.options.map((o) => (
              <button
                key={o.key}
                onClick={() => setAnswers((a) => ({ ...a, [q.id]: o.key }))}
                className={`flex w-full items-center gap-3 rounded-xl border px-4 py-3 text-left transition ${
                  answers[q.id] === o.key
                    ? "border-brand-500 bg-brand-500/10 text-white"
                    : "border-white/10 text-slate-300 hover:border-white/25"
                }`}
              >
                <span className="font-mono text-brand-300">{o.key}</span>
                {o.text}
              </button>
            ))}
          </div>
          <div className="mt-6 flex justify-between">
            <button
              disabled={current === 0}
              onClick={() => setCurrent((c) => c - 1)}
              className="text-sm text-slate-400 disabled:opacity-30"
            >
              ← Previous
            </button>
            <button
              disabled={current === session.questions.length - 1}
              onClick={() => setCurrent((c) => c + 1)}
              className="text-sm text-slate-400 disabled:opacity-30"
            >
              Next →
            </button>
          </div>
        </div>

        {/* Navigation sidebar */}
        <aside className="rounded-2xl border border-white/10 bg-white/[0.02] p-4">
          <p className="mb-3 text-xs uppercase tracking-wide text-slate-500">Navigator</p>
          <div className="grid grid-cols-5 gap-2">
            {session.questions.map((sq, i) => (
              <button
                key={sq.id}
                onClick={() => setCurrent(i)}
                className={`h-8 w-8 rounded-md text-xs ${
                  i === current
                    ? "bg-brand-500 text-indigoblack"
                    : answers[sq.id]
                    ? "bg-emerald-500/30 text-emerald-200"
                    : "bg-white/5 text-slate-400"
                }`}
              >
                {i + 1}
              </button>
            ))}
          </div>
        </aside>
      </div>
    </ProductShell>
  );
}
