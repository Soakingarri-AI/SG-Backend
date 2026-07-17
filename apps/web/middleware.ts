import { NextRequest, NextResponse } from "next/server";

/**
 * Subdomain router.
 *
 * Maps each `*.soakingarri.com` host onto an internal App Router segment so a
 * single Next.js deployment serves every product. The apex domain
 * (`soakingarri.com` / `www`) renders the marketing site untouched.
 *
 * Local dev: use the matching `*.localhost:3000` hosts, e.g.
 *   ask.localhost:3000, examflow.localhost:3000, ...
 */
const SUBDOMAIN_TO_SEGMENT: Record<string, string> = {
  ask: "/ask",
  examflow: "/examflow",
  afrosimulator: "/afrosimulator",
  memes: "/memes",
  infiniteparts: "/infiniteparts",
  factorizer: "/factorizer",
};

const ROOT_DOMAIN = process.env.NEXT_PUBLIC_ROOT_DOMAIN ?? "soakingarri.com";

function extractSubdomain(host: string): string | null {
  const hostname = host.split(":")[0]; // strip port
  // Local dev: <sub>.localhost
  if (hostname.endsWith(".localhost")) {
    return hostname.replace(".localhost", "");
  }
  // Production: <sub>.soakingarri.com
  if (hostname.endsWith(`.${ROOT_DOMAIN}`)) {
    const sub = hostname.slice(0, -1 * (ROOT_DOMAIN.length + 1));
    return sub === "www" ? null : sub;
  }
  return null;
}

export function middleware(req: NextRequest) {
  const host = req.headers.get("host") ?? "";
  const subdomain = extractSubdomain(host);

  if (!subdomain) {
    // Apex / www -> marketing site as-is.
    return NextResponse.next();
  }

  const segment = SUBDOMAIN_TO_SEGMENT[subdomain];
  if (!segment) {
    // Unknown subdomain -> marketing landing.
    return NextResponse.rewrite(new URL("/", req.url));
  }

  // Rewrite the path into the product's segment, preserving deep links.
  const url = req.nextUrl.clone();
  if (!url.pathname.startsWith(segment)) {
    url.pathname = `${segment}${url.pathname === "/" ? "" : url.pathname}`;
  }
  return NextResponse.rewrite(url);
}

export const config = {
  // Skip Next internals and static assets.
  matcher: ["/((?!_next|api|favicon.ico|assets|.*\\.[\\w]+$).*)"],
};
