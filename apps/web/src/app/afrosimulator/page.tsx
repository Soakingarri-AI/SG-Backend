"use client";

import { useEffect, useRef, useState } from "react";
import { ProductShell } from "@/components/ProductShell";
import { api } from "@/lib/api";

type Culture = "yoruba" | "igbo" | "hausa";
interface Turn {
  turn_index: number;
  culture: string;
  speaker: string;
  content: string;
  proverbs: string[];
}
interface Sim {
  id: string;
  status: "pending" | "running" | "completed" | "failed";
  topic: string;
  summary: string | null;
  turns: Turn[];
}

const CULTURES: Culture[] = ["yoruba", "igbo", "hausa"];

export default function AfroPage() {
  const [topic, setTopic] = useState("");
  const [selected, setSelected] = useState<Culture[]>(["yoruba", "igbo"]);
  const [sim, setSim] = useState<Sim | null>(null);
  const poll = useRef<ReturnType<typeof setInterval>>();

  useEffect(() => () => clearInterval(poll.current), []);

  function toggle(c: Culture) {
    setSelected((s) => (s.includes(c) ? s.filter((x) => x !== c) : [...s, c]));
  }

  async function run() {
    const { simulation_id } = await api<{ simulation_id: string }>("/afro/simulations", {
      method: "POST",
      json: { topic, cultures: selected, max_turns: 6 },
    });
    poll.current = setInterval(async () => {
      const s = await api<Sim>(`/afro/simulations/${simulation_id}`);
      setSim(s);
      if (s.status === "completed" || s.status === "failed") clearInterval(poll.current);
    }, 2000);
  }

  return (
    <ProductShell
      title="AfroSimulator"
      subtitle="Culturally-safe multi-agent conversations between Yoruba, Igbo, and Hausa personas."
    >
      <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6">
        <div className="mb-4 flex flex-wrap gap-2">
          {CULTURES.map((c) => (
            <button
              key={c}
              onClick={() => toggle(c)}
              className={`rounded-full px-4 py-1.5 text-sm capitalize ${
                selected.includes(c)
                  ? "bg-brand-500 text-indigoblack"
                  : "border border-white/10 text-slate-300"
              }`}
            >
              {c}
            </button>
          ))}
        </div>
        <div className="flex gap-3">
          <input
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="Topic e.g. 'the value of communal land ownership'"
            className="flex-1 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-white placeholder:text-slate-500 focus:border-brand-500/50 focus:outline-none"
          />
          <button
            onClick={run}
            disabled={!topic.trim() || selected.length < 2}
            className="rounded-xl bg-brand-500 px-6 py-3 font-medium text-indigoblack hover:bg-brand-300 disabled:opacity-50"
          >
            Simulate
          </button>
        </div>
      </div>

      {sim && (
        <div className="mt-8">
          <p className="mb-4 text-sm text-slate-400">
            Status: <span className="text-brand-300">{sim.status}</span>
          </p>
          <ol className="relative space-y-4 border-l border-white/10 pl-6">
            {sim.turns.map((t) => (
              <li key={t.turn_index} className="relative">
                <span className="absolute -left-[29px] top-1 h-3 w-3 rounded-full bg-brand-500" />
                <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
                  <p className="text-sm font-semibold capitalize text-brand-300">
                    {t.speaker} · {t.culture}
                  </p>
                  <p className="mt-1 text-slate-200">{t.content}</p>
                  {t.proverbs.length > 0 && (
                    <p className="mt-2 text-xs italic text-slate-500">
                      Proverbs: {t.proverbs.join(" · ")}
                    </p>
                  )}
                </div>
              </li>
            ))}
          </ol>
          {sim.summary && (
            <div className="mt-6 rounded-xl border border-brand-500/20 bg-brand-500/[0.05] p-5">
              <p className="text-sm font-semibold text-brand-300">Summary</p>
              <p className="mt-1 text-slate-200">{sim.summary}</p>
            </div>
          )}
        </div>
      )}
    </ProductShell>
  );
}
