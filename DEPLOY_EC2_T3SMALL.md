# Deploying SoakinGarri AI on a t3.small (Amazon Linux 2023)

A full, memory-conscious guide to hosting the platform on a single **`t3.small`**
EC2 instance running **Amazon Linux 2023 (AL2023)**.

> **Read this first — the 2 GB reality.**
> A `t3.small` has **2 vCPU and only 2 GB RAM**. That is enough to *run* the API +
> PostgreSQL + Redis comfortably, but **not** enough to *build* the Next.js web
> image on the box (the build alone needs ~1.5 GB and will be killed by the OOM
> reaper). So this guide uses the design that actually fits:
>
> - **On the EC2 box:** FastAPI **API** + **PostgreSQL** (pgvector) + **Redis**.
> - **Frontend:** hosted **off-box** (Vercel / AWS Amplify / S3 + CloudFront /
>   GitHub Pages — the repo already ships configs for these).
> - A **2 GB swap file** is added as a safety net for memory spikes.
>
> If you genuinely must run the web container on the same box too, see
> [Appendix A](#appendix-a--running-the-web-app-on-the-same-box).

---

## Contents

1. [Architecture](#1-architecture)
2. [Prerequisites](#2-prerequisites)
3. [IAM role for Bedrock + S3](#3-iam-role-for-bedrock--s3)
4. [Launch the t3.small instance](#4-launch-the-t3small-instance)
5. [DNS (Route 53)](#5-dns-route-53)
6. [First login + system prep (swap, Docker, Nginx)](#6-first-login--system-prep)
7. [Get the code and configure `.env`](#7-get-the-code-and-configure-env)
8. [Production Compose override (memory-tuned)](#8-production-compose-override-memory-tuned)
9. [Bring up the API stack](#9-bring-up-the-api-stack)
10. [Nginx reverse proxy + TLS](#10-nginx-reverse-proxy--tls)
11. [Host the frontend off-box](#11-host-the-frontend-off-box)
12. [Survive reboots](#12-survive-reboots)
13. [Operations & monitoring](#13-operations--monitoring)
14. [Memory tuning](#14-memory-tuning)
15. [Security checklist](#15-security-checklist)
16. [Troubleshooting](#16-troubleshooting)
17. [Appendix A — running the web app on the same box](#appendix-a--running-the-web-app-on-the-same-box)

---

## 1. Architecture

```
     Route 53                         Vercel / Amplify / S3+CloudFront
   api.soakingarri.com                soakingarri.com + *.soakingarri.com
          │                                     │  (frontend, off-box)
          ▼                                     ▼
 ┌──────────────────────────────┐        (calls the API over HTTPS)
 │  t3.small — Amazon Linux 2023 │◄──────────────┘
 │                               │
 │  Nginx (TLS)  ── :8000 ──► API  (FastAPI, 2 workers)
 │                               ├─ postgres :5432  (pgvector, internal only)
 │                               └─ redis    :6379  (internal only)
 │  + 2 GB swap file             │
 │  Docker Compose               │
 └───────────────┬───────────────┘
                 │ IAM instance role (no static keys)
                 ▼
        Amazon Bedrock  +  S3
```

**Approximate RAM budget on the box (runtime, not build):**

| Component | Typical | Compose `mem_limit` |
|-----------|---------|---------------------|
| PostgreSQL (pgvector) | 150–300 MB | 512m |
| Redis | 20–80 MB | 128m |
| FastAPI (2 uvicorn workers) | 250–450 MB | 640m |
| Nginx + OS | ~350 MB | — |
| **Total** | **~1.0–1.4 GB** | fits 2 GB + 2 GB swap |

---

## 2. Prerequisites

- AWS account; the `soakingarri.com` domain (or your own) manageable in **Route 53**.
- **Amazon Bedrock model access** granted in your region for the model in
  `AI_MODEL` (`claude-opus-4-8`) and embeddings (`amazon.titan-embed-text-v2:0`) —
  request under **Bedrock → Model access**. *(Prefer the Anthropic API? Set
  `AI_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` and skip the Bedrock IAM perms.)*
- An **S3 bucket** for user assets (default `soakingarri-assets`).
- A machine to build/deploy the frontend from (your laptop or CI) — **not** the
  t3.small.

---

## 3. IAM role for Bedrock + S3

Create an IAM **role** (trusted entity: EC2) so the box calls AWS with no static
keys. Attach this inline policy (scope S3 to your bucket):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "Bedrock",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": "*"
    },
    {
      "Sid": "S3Assets",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::soakingarri-assets",
        "arn:aws:s3:::soakingarri-assets/*"
      ]
    }
  ]
}
```

Name it `soakingarri-ec2-role`; attach at launch (step 4).

---

## 4. Launch the t3.small instance

### 4a. Create an SSH key pair (do this before launching)

You authenticate to the instance with an SSH key pair. AWS keeps the public half;
you download the private `.pem` **once** — it cannot be retrieved again, so store
it safely.

**Console:** EC2 → **Network & Security → Key Pairs → Create key pair**.
- Name: `soakingarri-key`
- Type: **RSA**
- Format: **`.pem`** (OpenSSH — for macOS/Linux/Windows OpenSSH) or **`.ppk`**
  (only if you use PuTTY on Windows)
- Click **Create** — the `.pem` downloads automatically.

**Or via the AWS CLI:**

```bash
aws ec2 create-key-pair \
  --key-name soakingarri-key \
  --key-type rsa \
  --query 'KeyMaterial' --output text > soakingarri-key.pem
```

**Lock down the private key file** (SSH refuses keys that are world-readable):

```bash
# macOS / Linux
chmod 400 soakingarri-key.pem
```

```powershell
# Windows (PowerShell) — remove inherited perms, grant only your user
icacls .\soakingarri-key.pem /inheritance:r
icacls .\soakingarri-key.pem /grant:r "$($env:USERNAME):(R)"
```

Keep this file private and backed up — anyone with it can SSH to your server, and
if you lose it you'll have to detach the volume or recreate the instance to regain
access.

### 4b. Launch settings

| Setting | Value |
|---------|-------|
| AMI | **Amazon Linux 2023** (x86_64) |
| Instance type | **t3.small** (2 vCPU / 2 GB) |
| Storage | **30 GB gp3** (Docker images for python + postgres + redis add up) |
| IAM instance profile | `soakingarri-ec2-role` |
| Key pair | **`soakingarri-key`** (from step 4a) |

> `t3.small` is **burstable** (CPU credits). Idle + light API traffic stays within
> baseline; sustained heavy AI request volume can exhaust credits. Enable
> **T3 Unlimited** if you expect spikes (small extra cost), or size up to
> `t3.medium` (4 GB) — which also lets you run the web container on-box.

**Security group** (inbound):

| Type | Port | Source |
|------|------|--------|
| SSH | 22 | *Your IP only* |
| HTTP | 80 | 0.0.0.0/0 (Let's Encrypt HTTP-01) |
| HTTPS | 443 | 0.0.0.0/0 |

Leave 5432 / 6379 / 8000 closed. Allocate an **Elastic IP** and associate it so
the public IP is stable.

---

## 5. DNS (Route 53)

In the hosted zone, create an **A record** for the API to the Elastic IP:

| Name | Type | Value |
|------|------|-------|
| `api.soakingarri.com` | A | `<elastic-ip>` |

The apex + product subdomains (`ask.`, `examflow.`, …) point at your **frontend
host** instead (step 11), not the EC2 box.

---

## 6. First login + system prep

SSH in as `ec2-user` (AL2023's default user), using the key from step 4a:

```bash
ssh -i soakingarri-key.pem ec2-user@<elastic-ip>
```

> Windows: run the same command in PowerShell (the built-in OpenSSH client), or
> use PuTTY with the `.ppk` key. First connection asks you to trust the host key —
> type `yes`.

### 6a. Create swap (do this first — protects every later step)

```bash
sudo dd if=/dev/zero of=/swapfile bs=1M count=2048 status=progress   # 2 GB
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
# Prefer RAM, use swap only under pressure:
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swap.conf
sudo sysctl -p /etc/sysctl.d/99-swap.conf
free -h        # verify: 2.0Gi mem + 2.0Gi swap
```

### 6b. Update + install Docker, Git, Nginx (AL2023 uses `dnf`)

```bash
sudo dnf update -y
sudo dnf install -y docker git nginx

# Docker
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user
newgrp docker                      # apply group in current shell

# Docker Compose v2 plugin (not bundled on AL2023)
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL \
  "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# AL2023's bundled Buildx (0.12.x) is too old for current Compose builds
# ("compose build requires buildx 0.17.0 or later"). Install a newer one into the
# user plugin dir (takes precedence over the system copy).
mkdir -p ~/.docker/cli-plugins
curl -SL \
  "https://github.com/docker/buildx/releases/download/v0.19.3/buildx-v0.19.3.linux-amd64" \
  -o ~/.docker/cli-plugins/docker-buildx
chmod +x ~/.docker/cli-plugins/docker-buildx

docker version && docker compose version && docker buildx version    # sanity check
```

---

## 7. Get the code and configure `.env`

### 7a. Give the server access to GitHub (deploy key)

If the repo is **public**, skip to 7b and clone over HTTPS. For a **private** repo,
add a **deploy key** — an SSH key pair generated *on the server* whose public half
is registered on the GitHub repo (read-only). This is safer than a personal token:
it's scoped to this one repo and grants no access to your GitHub account.

**On the EC2 box**, generate the key:

```bash
ssh-keygen -t ed25519 -C "soakingarri-ec2-deploy" -f ~/.ssh/github_deploy -N ""
cat ~/.ssh/github_deploy.pub        # copy this entire line
```

**On GitHub**: open the repo → **Settings → Deploy keys → Add deploy key**.
- Title: `soakingarri-ec2`
- Key: paste the `.pub` contents from above
- Leave **"Allow write access" unchecked** (the server only needs to pull)
- **Add key**

**Back on the box**, tell SSH to use that key for GitHub and trust the host:

```bash
cat >> ~/.ssh/config <<'EOF'
Host github.com
    HostName github.com
    User git
    IdentityFile ~/.ssh/github_deploy
    IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config

ssh -T git@github.com    # should greet you; type "yes" to trust github.com
```

> Use the **SSH** clone URL (`git@github.com:owner/repo.git`), not the HTTPS one,
> so the deploy key is used. Every later `git pull` (step 13) then works with no
> password prompt.

### 7b. Clone and configure

```bash
sudo mkdir -p /opt && sudo chown ec2-user:ec2-user /opt
cd /opt
git clone git@github.com:<owner>/<repo>.git soakingarri   # SSH URL (private repo)
# public repo alternative:  git clone https://github.com/<owner>/<repo>.git soakingarri
cd soakingarri
cp .env.example .env
chmod 600 .env
```

Edit `.env` for production. **Critical values:**

```dotenv
ENVIRONMENT=production

POSTGRES_USER=soakingarri
POSTGRES_PASSWORD=<long-random-password>
POSTGRES_DB=soakingarri
DATABASE_URL=postgresql+asyncpg://soakingarri:<long-random-password>@postgres:5432/soakingarri

REDIS_URL=redis://redis:6379/0

JWT_SECRET_KEY=<openssl rand -hex 32>
COOKIE_DOMAIN=.soakingarri.com

# Origins that may call the API = your frontend URLs.
CORS_ORIGINS=https://soakingarri.com,https://ask.soakingarri.com,https://examflow.soakingarri.com,https://afrosimulator.soakingarri.com,https://memes.soakingarri.com,https://infiniteparts.soakingarri.com,https://factorizer.soakingarri.com

AI_PROVIDER=bedrock
AWS_REGION=us-east-1
BEDROCK_REGION=us-east-1
# LEAVE BLANK — the IAM instance role provides credentials.
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
S3_BUCKET=soakingarri-assets

NEXT_PUBLIC_API_URL=https://api.soakingarri.com
NEXT_PUBLIC_ROOT_DOMAIN=soakingarri.com
```

Generate secrets on the box:

```bash
openssl rand -hex 32     # JWT_SECRET_KEY (paste the actual output, not <...>)
openssl rand -hex 24     # POSTGRES_PASSWORD — hex is URL-safe
```

> **Password gotcha:** Compose builds `DATABASE_URL` from `POSTGRES_PASSWORD`, so
> the password must contain **no URL-reserved characters** — avoid `# @ : / ?`
> (a `#` in particular silently truncates the connection string). Letters, digits,
> and `- _ .` are safe. `POSTGRES_PASSWORD` and the password inside `DATABASE_URL`
> must be identical.

---

## 8. Production Compose override (memory-tuned)

The committed `docker-compose.yml` is for **development** (hot-reload, bind-mounts,
exposed DB ports). Create **`docker-compose.prod.yml`** beside it — it switches the
API to its `prod` target, caps memory per service, and keeps datastores internal:

```yaml
# docker-compose.prod.yml — t3.small (2 GB) production overrides, API-only.
# Run with: docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d postgres redis api
services:
  postgres:
    ports: []                     # do not publish the DB port
    mem_limit: 512m
    # Trim Postgres memory for a 2 GB box.
    command:
      - "postgres"
      - "-c"
      - "shared_buffers=128MB"
      - "-c"
      - "max_connections=50"
      - "-c"
      - "effective_cache_size=384MB"

  redis:
    ports: []
    mem_limit: 128m
    command: ["redis-server", "--appendonly", "yes",
              "--maxmemory", "100mb", "--maxmemory-policy", "allkeys-lru"]

  api:
    build:
      target: prod                # immutable image, no --reload
    command: >
      sh -c "alembic upgrade head &&
             uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2"
    mem_limit: 640m
    environment:
      ENVIRONMENT: production
    # Keep the read-only reference data mount, drop the dev source mount.
    # (Compose replaces the whole volumes list, so restate what you keep.)
    volumes:
      - ./data:/data:ro
    # `!override` replaces the base file's "8000:8000" instead of appending to it
    # (Compose merges port lists — without this you get a duplicate-bind conflict).
    ports: !override
      - "127.0.0.1:8000:8000"     # Nginx on the host proxies to this
```

> The API image (~1 GB, mostly the Python deps) **does** build fine on a t3.small
> — it's only the **Next.js** build that doesn't fit, which is why the web service
> is omitted here.

---

## 9. Bring up the API stack

```bash
cd /opt/soakingarri
# Start ONLY the three backend services (note: no "web").
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build postgres redis api

docker compose ps                 # all healthy?
docker compose logs -f api        # watch alembic migrations run
```

Migrations run automatically on API start. Seed the personas + sample exam question:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  run --rm api python -m scripts.seed
```

Verify locally on the box:

```bash
curl -s http://127.0.0.1:8000/health
# {"status":"ok","service":"soakingarri-api","env":"production"}
```

---

## 10. Nginx reverse proxy + TLS

AL2023's Nginx auto-includes `/etc/nginx/conf.d/*.conf`. Create
`/etc/nginx/conf.d/soakingarri.conf`:

```nginx
server {
    server_name api.soakingarri.com;
    client_max_body_size 25m;             # allow asset uploads
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    listen 80;
}
```

Enable and start Nginx:

```bash
sudo nginx -t
sudo systemctl enable --now nginx
```

### Install Certbot (pip/venv — the reliable method on AL2023)

AL2023 doesn't ship Certbot in its default repos, so use the official pip install:

```bash
sudo dnf install -y python3 augeas-libs
sudo python3 -m venv /opt/certbot
sudo /opt/certbot/bin/pip install --upgrade pip
sudo /opt/certbot/bin/pip install certbot certbot-nginx
sudo ln -sf /opt/certbot/bin/certbot /usr/bin/certbot

# Issue + install the cert for the API host (edits the vhost, adds 443 + redirect).
sudo certbot --nginx -d api.soakingarri.com \
  --non-interactive --agree-tos -m you@example.com --redirect
```

Add auto-renewal (the pip install has no systemd timer of its own):

```bash
echo "0 3 * * * root /opt/certbot/bin/certbot renew --quiet --deploy-hook 'systemctl reload nginx'" \
  | sudo tee /etc/cron.d/certbot-renew
sudo /opt/certbot/bin/certbot renew --dry-run     # verify
```

Test: `https://api.soakingarri.com/health` and `https://api.soakingarri.com/docs`.

---

## 11. Host the frontend off-box

This repo is a **monorepo**: the same GitHub repo holds both `apps/api` (deployed
on the EC2 box above) and `apps/web` (deployed here). The EC2 box only runs the
backend; the frontend is built and served **separately from the same repo**.

The Next.js app is served **separately** from the API. Pick one (all point their
`NEXT_PUBLIC_API_URL` at `https://api.soakingarri.com`):

| Option | Best for | Repo guide |
|--------|----------|------------|
| **Vercel** | Zero-ops SSR + preview deploys | [`DEPLOY_VERCEL.md`](DEPLOY_VERCEL.md) |
| **AWS Amplify Hosting** | Staying inside AWS, CI from git | — |
| **S3 + CloudFront** | Cheapest, static export | — |
| **GitHub Pages** | Free static demo | [`DEPLOY_GITHUB_PAGES.md`](DEPLOY_GITHUB_PAGES.md) |

> **Monorepo setting (important):** when you connect the repo to Vercel/Amplify,
> set the project's **Root Directory** (Vercel) or **App root / monorepo path**
> (Amplify) to **`apps/web`** — otherwise the build runs at the repo root and
> fails to find the Next.js app. Set `NEXT_PUBLIC_API_URL=https://api.soakingarri.com`
> in that host's environment variables.

**DNS for the frontend:** in Route 53, point the apex and product subdomains at the
chosen host (Vercel/CloudFront target), e.g. a CNAME/ALIAS per subdomain or a
wildcard `*.soakingarri.com` → your CDN. Keep `api.` pointed at the EC2 box.

**CORS + cookies:** because the API sets cookies scoped to `.soakingarri.com`, keep
the frontend on a `*.soakingarri.com` host so the shared-session cookie is honoured,
and make sure each frontend origin is present in `CORS_ORIGINS`.

---

## 12. Survive reboots

`restart: unless-stopped` is already set on the services, and swap re-mounts from
`/etc/fstab`. Just ensure Docker starts on boot (done in step 6b via
`systemctl enable`). Confirm after a test reboot:

```bash
sudo reboot
# reconnect, then:
free -h && docker compose ps
```

---

## 13. Operations & monitoring

**Watch memory** (the thing most likely to bite on 2 GB):

```bash
free -h            # system RAM + swap
docker stats --no-stream   # per-container memory vs. limits
```

**Deploy an update:**

```bash
cd /opt/soakingarri && git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build postgres redis api
docker image prune -f       # reclaim disk from old layers
```

**Logs:**

```bash
docker compose logs -f api
```

**Database backup** (self-hosted Postgres — *your* responsibility; the `pgdata`
volume is the only copy otherwise):

```bash
docker compose exec postgres pg_dump -U soakingarri soakingarri \
  | gzip > ~/sg-$(date +%F).sql.gz
# Push off-box (uses the instance role):
aws s3 cp ~/sg-$(date +%F).sql.gz s3://soakingarri-assets/backups/
```

Automate it with a daily cron entry. **Losing the instance loses the DB** unless
you back up off-box (the #1 reason to move Postgres to RDS/Aurora later).

---

## 14. Memory tuning

Already applied in the Compose override (Postgres `shared_buffers=128MB`, Redis
`maxmemory=100mb`, `--workers 2`, per-service `mem_limit`s) and the system swappiness.
Additional levers if you see pressure:

- Drop the API to `--workers 1` (halves its footprint at some latency cost).
- Lower `RATE_LIMIT_PER_MINUTE` in `.env` to cap concurrent AI work.
- Move Postgres to **Amazon RDS** (`db.t4g.micro`) — frees ~400 MB on the box and
  gives you managed backups; then just point `DATABASE_URL` at the RDS endpoint and
  drop the `postgres` service from the compose command.

---

## 15. Security checklist

- [ ] Security group: only 22 (your IP), 80, 443 open; DB/Redis/app ports closed.
- [ ] `.env` is `chmod 600`, strong `POSTGRES_PASSWORD`, random `JWT_SECRET_KEY`.
- [ ] `ENVIRONMENT=production` (secure cookies + strict CORS active).
- [ ] `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` blank — IAM role used instead.
- [ ] Bedrock model access granted in `AWS_REGION`.
- [ ] TLS issued; `certbot renew --dry-run` passes; renewal cron in place.
- [ ] Off-box database backups scheduled.
- [ ] `sudo dnf update -y` on a schedule; SSH is key-only.

---

## 16. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `docker compose build` for **web** is `Killed` | OOM during Next.js build | Don't build web on-box — host frontend off-box (step 11) or build the image on your laptop/CI and pull it. |
| API container restarts / `OOMKilled` in `docker stats` | Memory spike over limit | Confirm swap is on (`free -h`); drop API to `--workers 1`; raise `mem_limit` only if headroom exists. |
| `bedrock ... AccessDenied` | Model access not granted, or wrong region | Grant model access in `BEDROCK_REGION`; confirm the IAM role is attached. |
| `certbot` fails HTTP-01 | Port 80 blocked or DNS not propagated | Open 80 in the SG; confirm `api.` A record resolves to the Elastic IP. |
| 502 from Nginx | API not up on `127.0.0.1:8000` | `docker compose ps` / `logs api`; check the migration step didn't fail. |
| CPU pegged, app sluggish | t3 CPU credits exhausted | Enable **T3 Unlimited** or move to `t3.medium`. |

---

## Appendix A — running the web app on the same box

Only if you insist on a single box. **Do not build the Next.js image on the
t3.small** — it will OOM. Instead:

1. **Build the web image elsewhere** (laptop/CI) and push to Amazon ECR:
   ```bash
   docker build -t <acct>.dkr.ecr.<region>.amazonaws.com/sg-web:latest \
     --target prod apps/web
   docker push <acct>.dkr.ecr.<region>.amazonaws.com/sg-web:latest
   ```
2. On the box, add a `web` service to `docker-compose.prod.yml` that **pulls the
   prebuilt image** (no `build:`) with a tight limit:
   ```yaml
   web:
     image: <acct>.dkr.ecr.<region>.amazonaws.com/sg-web:latest
     mem_limit: 448m
     environment:
       NODE_ENV: production
       NEXT_PUBLIC_API_URL: https://api.soakingarri.com
     ports:
       - "127.0.0.1:3000:3000"
   ```
   (Grant the instance role `ecr:GetAuthorizationToken` + pull perms, then
   `aws ecr get-login-password | docker login ...`.)
3. Add a second Nginx `server {}` block for the apex + product subdomains proxying
   to `127.0.0.1:3000`, and extend the `certbot` command with those `-d` names.

Even so, RAM will be **very** tight (runtime ~1.6–1.8 GB + swap). For a real
web + API single box, prefer **t3.medium (4 GB)** and follow
[`DEPLOY_EC2.md`](DEPLOY_EC2.md).
```
