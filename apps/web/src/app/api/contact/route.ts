import { NextResponse } from "next/server";

/**
 * Contact form handler.
 *
 * ⚠️ Delivery must be configured before launch. Set CONTACT_WEBHOOK_URL to an
 * endpoint that actually delivers the message (an email provider's API, a Slack
 * incoming webhook, or a small forwarder in front of SES).
 *
 * If it is NOT configured we deliberately return an error telling the sender to
 * email us directly, rather than returning a fake success and dropping their
 * message on the floor. Silently losing customer mail is worse than an honest
 * failure.
 */
const MAX_LEN = { name: 120, email: 320, subject: 200, message: 5000 };

interface ContactPayload {
  name?: string;
  email?: string;
  subject?: string;
  message?: string;
  /** Honeypot — real users never fill this. */
  company?: string;
}

export async function POST(req: Request) {
  let body: ContactPayload;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid request body." }, { status: 400 });
  }

  // Honeypot: pretend success so bots don't retune, but drop it.
  if (body.company) {
    return NextResponse.json({ ok: true });
  }

  const name = body.name?.trim() ?? "";
  const email = body.email?.trim() ?? "";
  const subject = body.subject?.trim() ?? "";
  const message = body.message?.trim() ?? "";

  if (!name || !email || !message) {
    return NextResponse.json(
      { error: "Name, email, and message are required." },
      { status: 400 },
    );
  }
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return NextResponse.json({ error: "Enter a valid email address." }, { status: 400 });
  }
  if (
    name.length > MAX_LEN.name ||
    email.length > MAX_LEN.email ||
    subject.length > MAX_LEN.subject ||
    message.length > MAX_LEN.message
  ) {
    return NextResponse.json({ error: "One or more fields are too long." }, { status: 400 });
  }

  const webhook = process.env.CONTACT_WEBHOOK_URL;
  if (!webhook) {
    console.error(
      "[contact] CONTACT_WEBHOOK_URL is not configured — message NOT delivered.",
    );
    return NextResponse.json(
      {
        error:
          "Our contact form isn't available right now. Please email us directly and we'll respond.",
      },
      { status: 503 },
    );
  }

  try {
    const res = await fetch(webhook, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, email, subject, message, receivedAt: new Date().toISOString() }),
    });
    if (!res.ok) throw new Error(`Webhook responded ${res.status}`);
  } catch (err) {
    console.error("[contact] delivery failed:", err);
    return NextResponse.json(
      { error: "We couldn't send your message. Please email us directly." },
      { status: 502 },
    );
  }

  return NextResponse.json({ ok: true });
}
