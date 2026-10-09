# Deploying the frontend to Vercel

The Next.js app (`apps/web`) deploys to Vercel; its `middleware.ts` handles the
`*.soakingarri.com` subdomain routing natively on Vercel's edge. The FastAPI
backend does **not** run on Vercel — host it separately (Fargate/Railway/Render)
and point the frontend at it.

---

## 1. Put the repo on GitHub

The project isn't a git repo yet. From `d:\SG-front`:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<you>/soakingarri.git
git push -u origin main
```

`.gitignore` already excludes `.env`, `node_modules`, and `.next`, so no secrets
are committed.

## 2. Import the project into Vercel

1. vercel.com → **Add New… → Project** → import the GitHub repo.
2. **Root Directory** → set to **`apps/web`**. This is the critical step — the
   app lives in a monorepo subfolder, and Vercel defaults to the repo root.
   Click *Edit* next to Root Directory and pick `apps/web`.
3. Framework preset auto-detects **Next.js**. Leave build/install commands as the
   detected defaults (or the `vercel.json` values).

## 3. Environment variables

In **Settings → Environment Variables**, add:

| Variable                  | Value                                          | Notes |
| ------------------------- | ---------------------------------------------- | ----- |
| `NEXT_PUBLIC_API_URL`     | `https://api.soakingarri.com`                  | Public URL of your deployed FastAPI backend. |
| `NEXT_PUBLIC_ROOT_DOMAIN` | `soakingarri.com`                              | Drives subdomain routing + product links. |
| `CONTACT_WEBHOOK_URL`     | your email/Slack webhook                        | Without it the contact form returns an honest 503. |

`NEXT_PUBLIC_*` values are baked in at build time, so **redeploy** after changing
them. Don't put backend secrets (JWT key, DB URL, AWS keys) here — those belong
to the API service, not the frontend.

## 4. Domains — the wildcard is the tricky part

In **Settings → Domains**, add three entries:

- `soakingarri.com`
- `www.soakingarri.com`
- `*.soakingarri.com`  ← the wildcard that makes every product subdomain work

**Wildcard domains require Vercel's nameservers** (this is true on every plan,
including Hobby). Vercel needs nameserver control to answer the DNS-01 challenge
that issues the wildcard TLS certificate — a plain CNAME can't do it. When you
add `*.soakingarri.com`, Vercel switches the domain to its nameservers
automatically and shows you two values:

```
ns1.vercel-dns.com
ns2.vercel-dns.com
```

Set **those** as the nameservers at your registrar (where you bought the domain),
replacing whatever is there now.

> ⚠️ Switching nameservers moves **all** DNS for `soakingarri.com` to Vercel. Any
> existing records you need to keep — most importantly the record pointing
> `api.soakingarri.com` at your backend, plus any MX/email records — must be
> re-created in Vercel's DNS tab, or they stop resolving. Copy them down before
> you switch.

DNS propagation to the new nameservers takes anywhere from minutes to ~48 hours.

## 5. Verify

Once the certificate issues and DNS propagates:

- `https://soakingarri.com` → marketing site
- `https://ask.soakingarri.com`, `https://examflow.soakingarri.com`, etc. →
  each product (middleware rewrites the host to its route segment)

## 6. Backend CORS + cookies

For the product pages to actually reach the API, the backend must allow the
production origins. `CORS_ORIGINS` in the API's env already lists the
`soakingarri.com` subdomains, and `COOKIE_DOMAIN=.soakingarri.com` lets the login
cookie span them — confirm both are set on the deployed backend.

---

### CLI alternative

```bash
npm i -g vercel
cd apps/web
vercel            # first run links the project — set root dir to current folder
vercel --prod
```

Add the same env vars and domains from the dashboard afterwards.
