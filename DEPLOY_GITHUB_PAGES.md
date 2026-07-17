# GitHub Pages demo (for approval / sharing)

This publishes `apps/web` as a **static demo** of the marketing site and all six
product UIs, so you can share one link (e.g. with your boss) without hosting a
server. A GitHub Actions workflow builds and deploys it automatically on every
push to `main`.

**Live URL (once enabled):**
`https://soakingarri-ai.github.io/SG-Backend/`

---

## One-time setup

1. Push has already added the workflow at `.github/workflows/pages.yml`.
2. On GitHub: **Settings → Pages → Build and deployment → Source → GitHub
   Actions**. (You only do this once.)
3. Push to `main` — or run the workflow manually from the **Actions** tab
   (**Deploy demo to GitHub Pages → Run workflow**).
4. Wait for the green check in **Actions**; the URL above goes live.

> The base path is set to `/SG-Backend` in the workflow because Pages serves a
> project repo under `/<repo-name>/`. **If you rename the repo, update
> `NEXT_PUBLIC_BASE_PATH` in `.github/workflows/pages.yml` to match**, or the
> site loads unstyled.

---

## What works in this demo

- The full marketing site — hero, products grid, platform section.
- **About, Contact, Privacy, Terms** pages.
- Clicking into all six products by path (`/ask`, `/examflow`, …) — the UI,
  layout, learning-mode toggles, exam navigator, wizard steps all render.

## What does NOT work in this demo (by design)

Because Pages is static and there is no backend or server:

- **Live AI features** — asking a question, generating an exam/meme/part/plan.
  These call the FastAPI backend, which isn't deployed here; buttons will show an
  error state instead of a result.
- **Login / signup** — needs the backend.
- **Contact form submission** — the form falls back to showing our email
  address instead of sending.
- **Subdomains** — there are none on a static host; products are reached by path
  instead (`.../SG-Backend/ask`), which is why navigation still works.

This is a look-and-flow demo. It is the right tool for design/UX approval, not
for demonstrating the AI itself.

## Making the AI features work later

Two options, both keeping this same frontend:

1. **Host the backend** (Fargate/Railway/Render), then rebuild the demo with
   `NEXT_PUBLIC_API_URL` pointing at it and add the Pages origin to the API's
   `CORS_ORIGINS`. Note browsers block a Pages **https** page from calling an
   **http** backend, so the backend must be https.
2. **Deploy the whole app to Vercel** instead (see `DEPLOY_VERCEL.md`), which
   runs the middleware, subdomains, and API routes for real.

---

## Local preview of the static build

```powershell
cd apps/web
$env:NEXT_PUBLIC_STATIC_DEMO="true"; $env:NEXT_PUBLIC_BASE_PATH="/SG-Backend"
npm run build
npx serve out -l 3000   # then open http://localhost:3000/SG-Backend/
```
