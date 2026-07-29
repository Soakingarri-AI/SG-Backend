# Deploying SoakinGarri AI on AWS EC2

This guide covers hosting the **whole stack** — FastAPI API, Next.js web app,
PostgreSQL (pgvector), and Redis — on a **single EC2 instance** using Docker
Compose, fronted by Nginx with free Let's Encrypt TLS.

> This is the pragmatic single-box path. The repo also ships a production-grade
> multi-service AWS topology (Aurora + ElastiCache + ECS Fargate + ALB +
> CloudFront) under [`infra/`](infra/) — use that when you outgrow one box. See
> [When to graduate off a single box](#when-to-graduate-off-a-single-box).

---

## Architecture on one EC2 box

```
                    Route 53  (soakingarri.com  +  *.soakingarri.com)
                                     │
                                     ▼
                         ┌───────────────────────┐
   Internet ── :443 ───► │   EC2 instance         │
                         │                        │
                         │   Nginx (TLS, vhosts)  │
                         │     ├─► web  :3000  (Next.js, prod)
                         │     └─► api  :8000  (FastAPI, prod)
                         │           ├─ postgres :5432  (pgvector, internal)
                         │           └─ redis    :6379  (internal)
                         │                        │
                         │   Docker Compose       │
                         └────────────┬───────────┘
                                      │ IAM instance role
                                      ▼
                          Amazon Bedrock  +  S3
```

- **Bedrock & S3** are reached with an **IAM instance role** — no access keys on
  the box.
- Postgres/Redis are **not** exposed publicly; only Nginx ports 80/443 are open.

---

## 0. Prerequisites

- An AWS account and the domain `soakingarri.com` (or your own) with DNS you can
  manage in **Route 53**.
- An **Amazon Bedrock** model access grant for the model in `AI_MODEL`
  (`claude-opus-4-8`) and the embedding model (`amazon.titan-embed-text-v2:0`) in
  your chosen region. Request it under **Bedrock → Model access**. *(If you'd
  rather use the Anthropic API instead, set `AI_PROVIDER=anthropic` and skip the
  Bedrock IAM permissions.)*
- An **S3 bucket** for user assets (default name `soakingarri-assets`).

---

## 1. Create the IAM role for the instance

Create an IAM **role** (trusted entity: EC2) so the box can call Bedrock and S3
without static keys.

Attach an inline policy (scope the S3 resource to your bucket):

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

Name it e.g. `soakingarri-ec2-role`. You'll attach it when launching the instance.

---

## 2. Launch the EC2 instance

| Setting | Recommendation |
|---------|----------------|
| AMI | Ubuntu Server 24.04 LTS (x86_64) |
| Instance type | `t3.large` (2 vCPU / 8 GB) minimum — the web build + Postgres + Redis + two apps are memory-hungry. `t3.medium` works for light use. |
| Storage | 30 GB gp3 (raise if you store many assets locally) |
| IAM instance profile | `soakingarri-ec2-role` (from step 1) |
| Key pair | Create/select one for SSH |

**Security group** — inbound rules:

| Type | Port | Source |
|------|------|--------|
| SSH | 22 | *Your IP only* |
| HTTP | 80 | 0.0.0.0/0 (needed for Let's Encrypt) |
| HTTPS | 443 | 0.0.0.0/0 |

Do **not** open 5432, 6379, 8000, or 3000 to the internet.

Allocate an **Elastic IP** and associate it with the instance so its public IP is
stable across reboots.

---

## 3. Point DNS at the instance (Route 53)

In the `soakingarri.com` hosted zone, create **A records** to the Elastic IP:

| Name | Type | Value |
|------|------|-------|
| `soakingarri.com` | A | `<elastic-ip>` |
| `*.soakingarri.com` | A | `<elastic-ip>` |

The wildcard covers every subdomain (`ask.`, `examflow.`, `afrosimulator.`,
`memes.`, `infiniteparts.`, `factorizer.`, `api.`).

---

## 4. Install Docker on the instance

SSH in (`ssh -i key.pem ubuntu@<elastic-ip>`), then:

```bash
sudo apt-get update && sudo apt-get upgrade -y
# Docker Engine + Compose plugin (official convenience script)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu          # run docker without sudo
newgrp docker                            # apply group in this shell
docker version && docker compose version # sanity check
```

---

## 5. Get the code and configure it

```bash
sudo mkdir -p /opt && sudo chown ubuntu:ubuntu /opt
cd /opt
git clone <your-repo-url> soakingarri
cd soakingarri
cp .env.example .env
```

Edit `.env` for production. **Critical changes:**

```dotenv
ENVIRONMENT=production

# Strong DB credentials (used by both Postgres and the API URL below)
POSTGRES_USER=soakingarri
POSTGRES_PASSWORD=<long-random-password>
POSTGRES_DB=soakingarri
DATABASE_URL=postgresql+asyncpg://soakingarri:<long-random-password>@postgres:5432/soakingarri

REDIS_URL=redis://redis:6379/0

# 32+ byte random secret: `openssl rand -hex 32`
JWT_SECRET_KEY=<output-of-openssl-rand-hex-32>
COOKIE_DOMAIN=.soakingarri.com

CORS_ORIGINS=https://soakingarri.com,https://ask.soakingarri.com,https://examflow.soakingarri.com,https://afrosimulator.soakingarri.com,https://memes.soakingarri.com,https://infiniteparts.soakingarri.com,https://factorizer.soakingarri.com

AI_PROVIDER=bedrock
AWS_REGION=us-east-1
BEDROCK_REGION=us-east-1
# Leave AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY BLANK — the IAM instance role
# supplies credentials automatically.
S3_BUCKET=soakingarri-assets

NEXT_PUBLIC_API_URL=https://api.soakingarri.com
NEXT_PUBLIC_ROOT_DOMAIN=soakingarri.com
```

> Keep `.env` off git (it already is in `.gitignore`). Treat this file as a
> secret — `chmod 600 .env`.

---

## 6. Add a production Compose override

The committed `docker-compose.yml` targets **development** (hot-reload, source
bind-mounts, exposed DB ports). Create `docker-compose.prod.yml` next to it to
override those for production:

```yaml
# docker-compose.prod.yml — production overrides for a single EC2 box.
# Usage: docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
services:
  postgres:
    ports: []          # do not publish the DB port
  redis:
    ports: []

  api:
    build:
      target: prod     # immutable image, no --reload
    command: >
      sh -c "alembic upgrade head &&
             uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4"
    # Drop the source bind-mount, but keep the read-only reference data mount
    # (Compose replaces the whole volumes list, so restate what you want to keep).
    volumes:
      - ./data:/data:ro
    environment:
      ENVIRONMENT: production
    # Bind only to localhost; Nginx (on the host) proxies to it.
    ports:
      - "127.0.0.1:8000:8000"

  web:
    build:
      target: prod
    volumes: []
    environment:
      NODE_ENV: production
      NEXT_PUBLIC_API_URL: https://api.soakingarri.com
    ports:
      - "127.0.0.1:3000:3000"
```

> Note: the override keeps `./data:/data:ro` so the Factorizer reference KB and
> Ask history sources stay available, while dropping the `./apps/api:/app` source
> mount that dev uses for hot-reload.

Build and start:

```bash
cd /opt/soakingarri
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose ps                          # all healthy?
docker compose logs -f api                  # watch migrations run
```

Migrations run automatically on `api` start (`alembic upgrade head`). Seed initial
dev data if you want the personas / sample question:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  run --rm api python -m scripts.seed
```

At this point the API is on `127.0.0.1:8000` and the web app on
`127.0.0.1:3000` — reachable only from the host. Nginx exposes them next.

---

## 7. Nginx reverse proxy + TLS

Install Nginx and Certbot on the **host** (not in Docker):

```bash
sudo apt-get install -y nginx certbot python3-certbot-nginx
```

Create `/etc/nginx/sites-available/soakingarri`:

```nginx
# API — api.soakingarri.com  →  container on :8000
server {
    server_name api.soakingarri.com;
    client_max_body_size 25m;               # allow asset uploads
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    listen 80;
}

# Web — root + every product subdomain  →  container on :3000
server {
    server_name soakingarri.com www.soakingarri.com
                ask.soakingarri.com examflow.soakingarri.com
                afrosimulator.soakingarri.com memes.soakingarri.com
                infiniteparts.soakingarri.com factorizer.soakingarri.com;
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    listen 80;
}
```

Enable it and issue certificates:

```bash
sudo ln -s /etc/nginx/sites-available/soakingarri /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

# One cert covering the API host and every web host. Certbot rewrites the vhosts
# to add 443 + HTTP→HTTPS redirects automatically.
sudo certbot --nginx \
  -d soakingarri.com -d www.soakingarri.com -d api.soakingarri.com \
  -d ask.soakingarri.com -d examflow.soakingarri.com \
  -d afrosimulator.soakingarri.com -d memes.soakingarri.com \
  -d infiniteparts.soakingarri.com -d factorizer.soakingarri.com
```

Certbot installs a systemd timer that auto-renews. Verify:

```bash
sudo certbot renew --dry-run
```

> **Wildcard alternative:** the per-subdomain `-d` list above uses HTTP-01
> validation and needs no DNS plugin. If you prefer a single `*.soakingarri.com`
> wildcard cert, use DNS-01 validation with the Route 53 plugin
> (`python3-certbot-dns-route53`) instead.

Now browse to `https://soakingarri.com` and `https://api.soakingarri.com/docs`.

---

## 8. Survive reboots

Docker's `restart: unless-stopped` (already set in the compose files) brings the
containers back after a reboot, provided the Docker service is enabled:

```bash
sudo systemctl enable docker
```

Nginx is enabled by default. That's enough for a single box. For stricter
control, wrap the compose up in a small systemd unit that runs on boot.

---

## 9. Operations

**Deploy an update:**

```bash
cd /opt/soakingarri
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker image prune -f
```

**Logs:**

```bash
docker compose logs -f api
docker compose logs -f web
```

**Database backup** (self-hosted Postgres — back it up yourself):

```bash
docker compose exec postgres \
  pg_dump -U soakingarri soakingarri | gzip > ~/sg-$(date +%F).sql.gz
```

Copy backups off-box (e.g. to S3) on a cron schedule. **The `pgdata` Docker volume
is your only copy otherwise — losing the instance loses the data.** This is the
single biggest reason to move Postgres to **Amazon RDS/Aurora** as you grow.

**Smoke test after deploy:**

```bash
curl -s https://api.soakingarri.com/health
# {"status":"ok","service":"soakingarri-api","env":"production"}
```

---

## 10. Security checklist

- [ ] Security group: only 22 (your IP), 80, 443 open. DB/Redis/app ports closed.
- [ ] `.env` has strong `POSTGRES_PASSWORD` and a random `JWT_SECRET_KEY`; file is `chmod 600`.
- [ ] `ENVIRONMENT=production` (enables secure cookies + strict CORS).
- [ ] No AWS keys in `.env` — the IAM instance role provides them.
- [ ] Bedrock model access granted in `AWS_REGION`.
- [ ] TLS issued and auto-renewing (`certbot renew --dry-run` passes).
- [ ] Automated off-box database backups.
- [ ] OS patched (`unattended-upgrades`), SSH key-only (password auth disabled).

---

## When to graduate off a single box

The single-EC2 setup is great for staging, demos, and early traffic. Move to the
managed topology in [`infra/`](infra/) (Aurora pgvector, ElastiCache, ECS Fargate
behind an ALB, CloudFront + S3) when you need:

- **Durability / managed backups** for the database (biggest win),
- **Horizontal scaling** or zero-downtime deploys,
- **High availability** across Availability Zones.

At that point the API and web images you already build here run unchanged on ECS —
only the datastores and networking move to managed services.
```
