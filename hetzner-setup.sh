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
# 4) Create .env
# -----------------------------
mkdir -p /root/spectrum/logs
cat > /root/spectrum/.env <<'EOF'
SUPABASE_URL=
SUPABASE_KEY=
SUPABASE_JWT_SECRET=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_JWK_PUBLIC_KEY=
GEMINI_API_KEY=
GEMINI_MODEL=gemini-1.5-flash
REDIS_URL=redis://localhost:6379/0
APPLE_SHARED_SECRET=
ANDROID_PACKAGE_NAME=
GOOGLE_SERVICE_ACCOUNT_JSON={}
REVENUECAT_WEBHOOK_SECRET=
EOF

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
ExecStart=/root/spectrum/.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

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
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# -----------------------------
# 6) Configure Nginx
# -----------------------------
cat > /etc/nginx/sites-available/spectrum <<'EOF'
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF
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
