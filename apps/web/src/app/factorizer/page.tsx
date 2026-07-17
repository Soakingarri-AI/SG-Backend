"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Bar,
  BarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ProductShell } from "@/components/ProductShell";
import { api } from "@/lib/api";

interface Machine {
  name: string;
  purpose: string;
  estimated_cost_usd: number;
  quantity: number;
}
interface CostItem {
  category: string;
  amount_usd: number;
}
interface Plan {
  id: string;
  plan_markdown: string;
  machines: Machine[];
  process_flow: string[];
  cost_breakdown: CostItem[];
}

const STEPS = ["Product", "Category", "Budget", "Automation", "Region", "Materials"] as const;

export default function FactorizerPage() {
  const [step, setStep] = useState(0);
  const [form, setForm] = useState({
    target_product: "",
    category: "",
    budget_usd: 50000,
    automation_level: "semi_automated",
    region: "West Africa",
    raw_materials: "" as string,
  });
  const [plan, setPlan] = useState<Plan | null>(null);
  const [loading, setLoading] = useState(false);

  function set<K extends keyof typeof form>(k: K, v: (typeof form)[K]) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function generate() {
    setLoading(true);
    const res = await api<Plan>("/factorizer/plans", {
      method: "POST",
      json: {
        ...form,
        raw_materials: form.raw_materials.split(",").map((s) => s.trim()).filter(Boolean),
      },
    });
    setPlan(res);
    setLoading(false);
  }

  if (plan) {
    return (
      <ProductShell title="Factorizer — Plan" subtitle={form.target_product}>
        {/* Cost breakdown chart */}
        <section className="mb-8 rounded-2xl border border-white/10 bg-white/[0.03] p-6">
          <h3 className="mb-4 font-semibold text-white">Estimated cost breakdown</h3>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={plan.cost_breakdown}>
                <XAxis dataKey="category" stroke="#94a3b8" fontSize={11} />
                <YAxis stroke="#94a3b8" fontSize={11} />
                <Tooltip
                  contentStyle={{ background: "#0b0e1a", border: "1px solid #333" }}
                />
                <Bar dataKey="amount_usd" fill="#e0a034" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>

        {/* Machine list */}
        <section className="mb-8 rounded-2xl border border-white/10 bg-white/[0.03] p-6">
          <h3 className="mb-4 font-semibold text-white">Machine list</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-slate-400">
                <tr>
                  <th className="py-2">Machine</th>
                  <th>Purpose</th>
                  <th className="text-right">Qty</th>
                  <th className="text-right">Est. cost</th>
                </tr>
              </thead>
              <tbody className="text-slate-200">
                {plan.machines.map((m, i) => (
                  <tr key={i} className="border-t border-white/5">
                    <td className="py-2 font-medium">{m.name}</td>
                    <td className="text-slate-400">{m.purpose}</td>
                    <td className="text-right">{m.quantity}</td>
                    <td className="text-right font-mono text-brand-300">
                      ${m.estimated_cost_usd.toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* Process flow */}
        <section className="mb-8 rounded-2xl border border-white/10 bg-white/[0.03] p-6">
          <h3 className="mb-4 font-semibold text-white">Process flow</h3>
          <div className="flex flex-wrap items-center gap-2">
            {plan.process_flow.map((s, i) => (
              <span key={i} className="flex items-center gap-2">
                <span className="rounded-lg border border-brand-500/30 bg-brand-500/10 px-3 py-1.5 text-sm text-brand-200">
                  {s}
                </span>
                {i < plan.process_flow.length - 1 && (
                  <span className="text-slate-600">→</span>
                )}
              </span>
            ))}
          </div>
        </section>

        {/* Full 15-section markdown plan */}
        <section className="prose prose-invert max-w-none rounded-2xl border border-white/10 bg-white/[0.02] p-6">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{plan.plan_markdown}</ReactMarkdown>
        </section>
      </ProductShell>
    );
  }

  return (
    <ProductShell
      title="Factorizer"
      subtitle="A guided wizard that returns a 15-section factory setup plan."
    >
      {/* Stepper */}
      <div className="mb-6 flex flex-wrap gap-2">
        {STEPS.map((s, i) => (
          <span
            key={s}
            className={`rounded-full px-3 py-1 text-xs ${
              i === step
                ? "bg-brand-500 text-indigoblack"
                : i < step
                ? "bg-emerald-500/20 text-emerald-300"
                : "bg-white/5 text-slate-500"
            }`}
          >
            {i + 1}. {s}
          </span>
        ))}
      </div>

      <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6">
        {step === 0 && (
          <Field label="Target product">
            <input className={inputCls} value={form.target_product} onChange={(e) => set("target_product", e.target.value)} placeholder="e.g. sachet water" />
          </Field>
        )}
        {step === 1 && (
          <Field label="Category">
            <input className={inputCls} value={form.category} onChange={(e) => set("category", e.target.value)} placeholder="e.g. beverages" />
          </Field>
        )}
        {step === 2 && (
          <Field label={`Budget (USD): $${form.budget_usd.toLocaleString()}`}>
            <input type="range" min={5000} max={1000000} step={5000} value={form.budget_usd} onChange={(e) => set("budget_usd", Number(e.target.value))} className="w-full accent-brand-500" />
          </Field>
        )}
        {step === 3 && (
          <Field label="Automation level">
            <select className={inputCls} value={form.automation_level} onChange={(e) => set("automation_level", e.target.value)}>
              <option value="manual">Manual</option>
              <option value="semi_automated">Semi-automated</option>
              <option value="fully_automated">Fully automated</option>
            </select>
          </Field>
        )}
        {step === 4 && (
          <Field label="Region">
            <input className={inputCls} value={form.region} onChange={(e) => set("region", e.target.value)} />
          </Field>
        )}
        {step === 5 && (
          <Field label="Raw materials (comma separated)">
            <input className={inputCls} value={form.raw_materials} onChange={(e) => set("raw_materials", e.target.value)} placeholder="water, PET film, ink" />
          </Field>
        )}

        <div className="mt-6 flex justify-between">
          <button disabled={step === 0} onClick={() => setStep((s) => s - 1)} className="text-sm text-slate-400 disabled:opacity-30">
            ← Back
          </button>
          {step < STEPS.length - 1 ? (
            <button onClick={() => setStep((s) => s + 1)} className="rounded-lg bg-brand-500 px-5 py-2 text-sm font-medium text-indigoblack hover:bg-brand-300">
              Next →
            </button>
          ) : (
            <button onClick={generate} disabled={loading} className="rounded-lg bg-brand-500 px-5 py-2 text-sm font-medium text-indigoblack hover:bg-brand-300 disabled:opacity-50">
              {loading ? "Generating plan…" : "Generate factory plan"}
            </button>
          )}
        </div>
      </div>
    </ProductShell>
  );
}

const inputCls =
  "w-full rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-white placeholder:text-slate-500 focus:border-brand-500/50 focus:outline-none";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-2 block text-sm text-slate-300">{label}</span>
      {children}
    </label>
  );
}
