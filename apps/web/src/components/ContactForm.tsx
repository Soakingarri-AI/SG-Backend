"use client";

import { useState } from "react";
import { COMPANY } from "@/lib/company";

type Status = "idle" | "sending" | "sent" | "error";

const inputCls =
  "w-full rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-white placeholder:text-slate-500 focus:border-brand-500/50 focus:outline-none";

export function ContactForm() {
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setStatus("sending");
    setError(null);

    const data = Object.fromEntries(new FormData(e.currentTarget));
    try {
      const res = await fetch("/api/contact", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error ?? "Something went wrong.");
      setStatus("sent");
    } catch (err) {
      setStatus("error");
      setError(err instanceof Error ? err.message : "Something went wrong.");
    }
  }

  if (status === "sent") {
    return (
      <div className="rounded-2xl border border-emerald-500/30 bg-emerald-500/[0.06] p-8">
        <h2 className="font-display text-xl font-semibold text-white">
          Message received
        </h2>
        <p className="mt-2 text-slate-300">
          Thanks — we&apos;ll get back to you at the address you gave us.
        </p>
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="rounded-2xl border border-white/10 bg-white/[0.03] p-8"
    >
      {/* Honeypot — visually hidden, ignored by real users. */}
      <input
        type="text"
        name="company"
        tabIndex={-1}
        autoComplete="off"
        aria-hidden="true"
        className="absolute left-[-9999px] h-0 w-0 opacity-0"
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="mb-2 block text-sm text-slate-300">Name</span>
          <input name="name" required maxLength={120} className={inputCls} placeholder="Your name" />
        </label>
        <label className="block">
          <span className="mb-2 block text-sm text-slate-300">Email</span>
          <input
            name="email"
            type="email"
            required
            maxLength={320}
            className={inputCls}
            placeholder="you@example.com"
          />
        </label>
      </div>

      <label className="mt-4 block">
        <span className="mb-2 block text-sm text-slate-300">Subject</span>
        <input name="subject" maxLength={200} className={inputCls} placeholder="What's this about?" />
      </label>

      <label className="mt-4 block">
        <span className="mb-2 block text-sm text-slate-300">Message</span>
        <textarea
          name="message"
          required
          rows={6}
          maxLength={5000}
          className={`${inputCls} resize-y`}
          placeholder="Tell us what's on your mind…"
        />
      </label>

      {error && (
        <p className="mt-4 rounded-lg border border-rose-500/30 bg-rose-500/[0.06] p-3 text-sm text-rose-300">
          {error} You can reach us directly at{" "}
          <a href={`mailto:${COMPANY.email.general}`} className="underline">
            {COMPANY.email.general}
          </a>
          .
        </p>
      )}

      <button
        type="submit"
        disabled={status === "sending"}
        className="mt-6 rounded-full bg-brand-500 px-6 py-3 font-medium text-indigoblack transition hover:bg-brand-300 disabled:opacity-50"
      >
        {status === "sending" ? "Sending…" : "Send message"}
      </button>
    </form>
  );
}
