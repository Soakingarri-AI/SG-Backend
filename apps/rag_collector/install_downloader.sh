#!/usr/bin/env bash
# =============================================================================
# Install the hourly corpus job as a systemd timer on the collector box:
#   1. backfill_origin.py  judges authors with no author_is_african verdict yet
#   2. pdf_downloader.py   copies every African-author PDF into S3
# Step 1 runs first so rows it marks TRUE are downloaded in the same hour.
# Idempotent: re-running rewrites the units and restarts the timer.
#
#   cd ~/rag_collector && ./install_downloader.sh
#
# Needs the instance role (soakingarri-rag-collector) for S3 access; without
# credentials each run exits 2 and logs why, touching nothing.
# =============================================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="$(stat -c %U "${APP_DIR}")"
UNIT=soakingarri-pdf-downloader

# Amazon Linux mounts /tmp as tmpfs (RAM). PDFs run to 150 MB and the box has
# ~900 MB, so downloads spool to disk instead.
mkdir -p "${APP_DIR}/tmp" "${APP_DIR}/logs"

sudo tee "/etc/systemd/system/${UNIT}.service" >/dev/null <<EOF
[Unit]
Description=SoakinGarri: judge new authors, then download African-author PDFs into S3
Documentation=file://${APP_DIR}/README.md
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=-${APP_DIR}/collector.env
Environment=PYTHONUNBUFFERED=1
Environment=TMPDIR=${APP_DIR}/tmp
# "-": a failed origin pass must not stop the downloads.
ExecStartPre=-${APP_DIR}/.venv/bin/python ${APP_DIR}/backfill_origin.py --workers 4
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/pdf_downloader.py --workers 3
# Never starve the collector, which shares this small box.
Nice=10
MemoryMax=350M
TimeoutStartSec=3h
EOF

sudo tee "/etc/systemd/system/${UNIT}.timer" >/dev/null <<EOF
[Unit]
Description=Run ${UNIT} hourly

[Timer]
OnBootSec=10min
OnUnitInactiveSec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now "${UNIT}.timer"
systemctl list-timers "${UNIT}.timer" --no-pager
echo
echo "Run now:  sudo systemctl start ${UNIT}.service"
echo "Logs:     sudo journalctl -u ${UNIT} -f   (or ${APP_DIR}/logs/pdf_downloader.log)"
