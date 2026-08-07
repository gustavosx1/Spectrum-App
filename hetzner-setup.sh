#!/bin/bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

# -----------------------------
# 1) System packages
# -----------------------------
apt update
apt upgrade -y

apt install -y \
  git \
  python3 \
  python3-venv \
  python3-pip \
  nginx \
  redis-server \
  curl \
  certbot \
  python3-certbot-nginx \
  build-essential \
  libnss3 \
  libdrm2 \
  libxkbcommon0 \
  libxss1 \
  libx11-xcb1

# Some Ubuntu versions renamed these packages; install whichever exists.
for pkg in \
  libatk1.0-0 libatk1.0-0t64 \
  libatk-bridge2.0-0 libatk-bridge2.0-0t64 \
  libcups2 libcups2t64 \
  libgtk-3-0 libgtk-3-0t64 \
  libgbm1 \
  libasound2 libasound2t64; do
  if apt-cache policy "$pkg" 2>/dev/null | grep -q 'Candidate:'; then
    apt install -y "$pkg" || true
  fi
done

# -----------------------------
# 2) Clone repo
# -----------------------------
cd /root
if [ ! -d /root/spectrum ]; then
  git clone https://github.com/gustavosx1/Spectrum-App.git /root/spectrum
else
  cd /root/spectrum && git pull
fi

# -----------------------------
# 3) Python env & dependencies
# -----------------------------
cd /root/spectrum
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
python -m playwright install chromium

# -----------------------------
# 4) Validate production config
# -----------------------------
mkdir -p /root/spectrum/logs
if [[ ! -f /root/spectrum/.env ]]; then
  echo "Missing /root/spectrum/.env. Create it from .env.example with production values before rerunning." >&2
  exit 1
fi
chmod 600 /root/spectrum/.env

: "${SPECTRUM_API_DOMAIN:?Set SPECTRUM_API_DOMAIN, for example api.prismanews.com.br}"
: "${LETSENCRYPT_EMAIL:?Set LETSENCRYPT_EMAIL for TLS certificate issuance}"

# -----------------------------
# 5) Create systemd services
# -----------------------------
cat > /etc/systemd/system/spectrum-api.service <<'EOF'
[Unit]
Description=Spectrum API
After=network.target redis-server.service

[Service]
Type=simple
WorkingDirectory=/root/spectrum
Environment=PATH=/root/spectrum/.venv/bin
ExecStart=/bin/bash -lc 'source /root/spectrum/.venv/bin/activate && python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=127.0.0.1'
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/spectrum-worker.service <<'EOF'
[Unit]
Description=Spectrum Celery Worker
After=network.target redis-server.service

[Service]
Type=simple
WorkingDirectory=/root/spectrum
Environment=PATH=/root/spectrum/.venv/bin
ExecStart=/root/spectrum/.venv/bin/celery -A worker.celery_app worker --loglevel=info
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# -----------------------------
# 6) Configure Nginx
# -----------------------------
cat > /etc/nginx/conf.d/spectrum-rate-limit.conf <<'EOF'
limit_req_zone $binary_remote_addr zone=spectrum_auth:10m rate=10r/m;
EOF'

cat > /etc/nginx/sites-available/spectrum <<'EOF'
server {
    listen 80;
  server_name SPECTRUM_API_DOMAIN_PLACEHOLDER;

    location / {
        proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
        proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

  location = /auth/refresh {
    limit_req zone=spectrum_auth burst=5 nodelay;
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
EOF
sed -i "s/SPECTRUM_API_DOMAIN_PLACEHOLDER/${SPECTRUM_API_DOMAIN}/g" /etc/nginx/sites-available/spectrum
ln -sf /etc/nginx/sites-available/spectrum /etc/nginx/sites-enabled/spectrum
nginx -t

# -----------------------------
# 7) Enable services and cron
# -----------------------------
systemctl daemon-reload
systemctl enable --now redis-server nginx
systemctl enable --now spectrum-api
systemctl enable --now spectrum-worker
systemctl restart nginx

certbot --nginx --non-interactive --agree-tos --redirect \
  --email "$LETSENCRYPT_EMAIL" \
  -d "$SPECTRUM_API_DOMAIN"

cat > /root/spectrum/run_scraper.sh <<'EOF'
#!/bin/bash
set -e
cd /root/spectrum
. .venv/bin/activate
/usr/bin/flock -n /tmp/spectrum-scraper.lock /root/spectrum/.venv/bin/python run_scraper.py --verbose >> /root/spectrum/logs/scraper.log 2>&1
EOF
chmod +x /root/spectrum/run_scraper.sh

(crontab -l 2>/dev/null | grep -v '/root/spectrum/run_scraper.sh' || true; echo '0 * * * * /root/spectrum/run_scraper.sh') | crontab -

echo "Setup finished."
echo "Test with: curl http://127.0.0.1:8000/health"
