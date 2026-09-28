#!/usr/bin/env bash
#
# setup_ec2.sh - one-shot provisioning for the dork/sheet collector.
#
# Installs Python 3.10+, builds a virtualenv, installs requirements, and
# registers a systemd unit so the scraper survives SSH disconnects, crashes and
# reboots.
#
# Supports both EC2 base images:
#   * Amazon Linux 2023 / RHEL / Fedora  (dnf, user ec2-user, ships Python 3.9)
#   * Ubuntu / Debian                    (apt, user ubuntu)
# Amazon Linux 2023 defaults to Python 3.9, which is too old, so 3.11 is pulled
# from the distro repos and used explicitly rather than replacing the system
# python3 (dnf itself depends on it - swapping it out breaks the box).
#
# Usage (from the repo directory, as the ubuntu user):
#
#     chmod +x setup_ec2.sh
#     ./setup_ec2.sh
#
# Then:
#     sudo systemctl status  soakingarri-scraper
#     sudo journalctl -u soakingarri-scraper -f
#
set -Eeuo pipefail

# --------------------------------------------------------------------------- #
#  Settings
# --------------------------------------------------------------------------- #

SERVICE_NAME="soakingarri-scraper"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${APP_DIR}/.venv"
PYTHON_BIN="python3"
RUN_USER="${SUDO_USER:-$(id -un)}"
RUN_GROUP="$(id -gn "${RUN_USER}")"
CREDENTIALS="${APP_DIR}/service_account.json"

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

trap 'die "Setup failed on line ${LINENO}"' ERR

# --------------------------------------------------------------------------- #
#  Preflight
# --------------------------------------------------------------------------- #

[[ "$(uname -s)" == "Linux" ]] || die "This script targets Ubuntu/Linux."
command -v systemctl >/dev/null 2>&1 || die "systemd not available on this host."

log "Provisioning ${SERVICE_NAME}"
log "  app dir : ${APP_DIR}"
log "  run as  : ${RUN_USER}:${RUN_GROUP}"

# --------------------------------------------------------------------------- #
#  1. System packages
# --------------------------------------------------------------------------- #

if command -v dnf >/dev/null 2>&1; then
    PKG="dnf"
elif command -v yum >/dev/null 2>&1; then
    PKG="yum"
elif command -v apt-get >/dev/null 2>&1; then
    PKG="apt"
else
    die "No supported package manager (dnf/yum/apt-get) found."
fi
log "Package manager: ${PKG}"

install_python_310_plus() {
    # Returns via the global PYTHON_BIN.
    local major minor
    major=$("${PYTHON_BIN}" -c 'import sys; print(sys.version_info[0])')
    minor=$("${PYTHON_BIN}" -c 'import sys; print(sys.version_info[1])')
    if (( major > 3 || (major == 3 && minor >= 10) )); then
        return 0
    fi

    warn "Detected Python ${major}.${minor}; the collector needs 3.10+"
    case "${PKG}" in
        dnf|yum)
            # Install alongside the system python3 - never replace it, dnf
            # itself runs on it.
            sudo "${PKG}" install -y python3.11 python3.11-pip python3.11-devel
            PYTHON_BIN="python3.11"
            ;;
        apt)
            sudo apt-get install -y software-properties-common
            sudo add-apt-repository -y ppa:deadsnakes/ppa
            sudo apt-get update -y
            sudo apt-get install -y python3.11 python3.11-venv python3.11-dev
            PYTHON_BIN="python3.11"
            ;;
    esac
}

log "Updating system packages"
if [[ "${PKG}" == "apt" ]]; then
    export DEBIAN_FRONTEND=noninteractive
    sudo apt-get update -y
    sudo apt-get install -y \
        python3 python3-pip python3-venv python3-dev \
        build-essential ca-certificates curl git tzdata
else
    # Note: do NOT ask for `curl` here. Amazon Linux 2023 ships `curl-minimal`,
    # and the full `curl` package conflicts with it - dnf aborts the whole
    # transaction. The curl binary is already present either way.
    sudo "${PKG}" install -y \
        python3 python3-pip python3-devel \
        gcc gcc-c++ make ca-certificates git tar
fi

install_python_310_plus
command -v "${PYTHON_BIN}" >/dev/null 2>&1 || die "${PYTHON_BIN} not on PATH after install."
log "Using $(${PYTHON_BIN} --version)"

# --------------------------------------------------------------------------- #
#  2. Virtualenv + dependencies
# --------------------------------------------------------------------------- #

log "Creating virtualenv at ${VENV_DIR}"
"${PYTHON_BIN}" -m venv "${VENV_DIR}"

log "Installing Python dependencies"
"${VENV_DIR}/bin/pip" install --upgrade pip setuptools wheel
"${VENV_DIR}/bin/pip" install -r "${APP_DIR}/requirements.txt"

mkdir -p "${APP_DIR}/logs"

# --------------------------------------------------------------------------- #
#  3. Credentials check
# --------------------------------------------------------------------------- #

if [[ -f "${CREDENTIALS}" ]]; then
    chmod 600 "${CREDENTIALS}"
    log "Found service account key (permissions tightened to 600)"
else
    warn "No service_account.json in ${APP_DIR}."
    warn "Upload it before starting the service, e.g.:"
    warn "    scp -i key.pem service_account.json ubuntu@<EC2_IP>:${APP_DIR}/"
    warn "Remember to share the target Sheet with the service account's client_email."
fi

# --------------------------------------------------------------------------- #
#  4. systemd unit
# --------------------------------------------------------------------------- #

UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
log "Writing ${UNIT_PATH}"

sudo tee "${UNIT_PATH}" >/dev/null <<UNIT
[Unit]
Description=Soakingarri RAG dork collector -> Google Sheets
Documentation=file://${APP_DIR}/README.md
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${APP_DIR}

Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=GOOGLE_APPLICATION_CREDENTIALS=${CREDENTIALS}
# Override the sheet target without editing the unit:
#   sudo systemctl edit ${SERVICE_NAME}
# Environment=SHEET_ID=...
# Environment=WORKSHEET_NAME=Sheet1
# Environment=LOG_LEVEL=INFO

ExecStart=${VENV_DIR}/bin/python ${APP_DIR}/dork_sheet_collector.py

# Long-haul resilience: always come back, but back off so a hard-failing
# config (bad key, unshared sheet) does not hammer the API in a restart loop.
Restart=always
RestartSec=30
StartLimitIntervalSec=0

# SIGTERM lets the collector flush its buffered rows before dying.
KillSignal=SIGTERM
TimeoutStopSec=90

StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

# Light hardening - the process only needs outbound network and its own dir.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=${APP_DIR}

[Install]
WantedBy=multi-user.target
UNIT

log "Reloading systemd and enabling the service"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"

# --------------------------------------------------------------------------- #
#  5. Smoke test + start
# --------------------------------------------------------------------------- #

if [[ -f "${CREDENTIALS}" ]]; then
    log "Running a dry-run smoke test (no rows will be written)"
    # --max-queries keeps this to seconds; a full cycle is ~160 queries.
    if ! "${VENV_DIR}/bin/python" "${APP_DIR}/dork_sheet_collector.py" \
        --dry-run --once --max-queries 2 --results 3 --log-level INFO; then
        warn "Smoke test failed - inspect the output above before starting."
        warn "Most common cause: the Sheet is not shared with the service account."
        exit 1
    fi

    log "Starting ${SERVICE_NAME}"
    sudo systemctl restart "${SERVICE_NAME}"
    sleep 3
    sudo systemctl --no-pager --full status "${SERVICE_NAME}" || true
else
    warn "Service enabled but NOT started (no credentials yet)."
    warn "After uploading the key run: sudo systemctl start ${SERVICE_NAME}"
fi

cat <<EOF

------------------------------------------------------------------
Setup complete.

  Follow logs      : sudo journalctl -u ${SERVICE_NAME} -f
  File log         : tail -f ${APP_DIR}/logs/collector.log
  Restart          : sudo systemctl restart ${SERVICE_NAME}
  Stop             : sudo systemctl stop ${SERVICE_NAME}
  Disable at boot  : sudo systemctl disable ${SERVICE_NAME}
------------------------------------------------------------------
EOF
