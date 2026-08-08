# WaffleBerry minimal production deployment

Replace every `example.com`, password, repository URL, and placeholder before running commands. The application uses bearer-token authentication, so no cross-origin cookies are required.

## 1. Create and secure the server

Create an Ubuntu LTS Hetzner VPS, add your SSH public key, point `api.example.com` to its public IP, then connect:

```powershell
ssh root@SERVER_IP
```

On the server:

```bash
apt update && apt upgrade -y
apt install -y python3 python3-venv python3-pip postgresql nginx git certbot python3-certbot-nginx ufw
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
adduser --disabled-password --gecos "" waffleberry
install -d -o waffleberry -g waffleberry /opt/waffleberry
install -d -m 750 -o root -g waffleberry /etc/waffleberry
```

## 2. Create PostgreSQL credentials

Choose a unique generated password and replace `CHANGE_ME_DATABASE_PASSWORD`:

```bash
sudo -u postgres psql
```

```sql
CREATE ROLE waffleberry_app LOGIN PASSWORD 'CHANGE_ME_DATABASE_PASSWORD';
CREATE DATABASE waffleberry OWNER waffleberry_app;
REVOKE ALL ON DATABASE waffleberry FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE waffleberry TO waffleberry_app;
\q
```

## 3. Install the backend

```bash
sudo -u waffleberry git clone BACKEND_REPOSITORY_URL /opt/waffleberry/WaffleBerry_backend
cd /opt/waffleberry/WaffleBerry_backend/backend
sudo -u waffleberry python3 -m venv .venv
sudo -u waffleberry .venv/bin/python -m pip install --upgrade pip
sudo -u waffleberry .venv/bin/python -m pip install -r requirements.txt
```

Create a long JWT secret locally with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Then create the root-owned environment file:

```bash
sudoedit /etc/waffleberry/backend.env
```

```dotenv
APP_NAME=Waffle Berry Backend
DEBUG=false
DATABASE_URL=postgresql+psycopg2://waffleberry_app:CHANGE_ME_DATABASE_PASSWORD@127.0.0.1:5432/waffleberry
API_V1_PREFIX=/api/v1
CORS_ORIGINS=https://www.example.com,https://example-project.vercel.app
JWT_SECRET_KEY=CHANGE_ME_LONG_RANDOM_SECRET
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
AI_PROVIDER=openai
AI_MODEL=CHANGE_ME_MODEL_NAME
OPENAI_API_KEY=CHANGE_ME_OPENAI_KEY
AI_CONNECT_TIMEOUT_SECONDS=10
AI_READ_TIMEOUT_SECONDS=90
AI_RETRY_MAX_RETRIES=2
AI_RETRY_BASE_DELAY_SECONDS=0.25
AI_RETRY_MAX_DELAY_SECONDS=2
AI_RETRY_JITTER_SECONDS=0.15
AI_MAX_CONTEXT_MESSAGES=24
MEMORY_GROUNDING_MAX_MEMORIES=8
MEMORY_GROUNDING_MAX_ESTIMATED_TOKENS=1500
MEMORY_GROUNDING_MAX_CHARACTERS=6000
```

```bash
chown root:waffleberry /etc/waffleberry/backend.env
chmod 640 /etc/waffleberry/backend.env
cd /opt/waffleberry/WaffleBerry_backend/backend
set -a; source /etc/waffleberry/backend.env; set +a
.venv/bin/python -m alembic upgrade head
unset DATABASE_URL JWT_SECRET_KEY OPENAI_API_KEY
```

## 4. Start the backend with systemd

```bash
cp /opt/waffleberry/WaffleBerry_backend/deploy/waffleberry-backend.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now waffleberry-backend
systemctl status waffleberry-backend --no-pager
journalctl -u waffleberry-backend -n 50 --no-pager
curl --fail http://127.0.0.1:8000/health
```

The local health response must be `{"status":"ok"}`.

## 5. Configure Nginx and HTTPS

Edit `server_name` in the supplied file, then install it:

```bash
cp /opt/waffleberry/WaffleBerry_backend/deploy/nginx-waffleberry.conf /etc/nginx/sites-available/waffleberry
ln -s /etc/nginx/sites-available/waffleberry /etc/nginx/sites-enabled/waffleberry
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx
curl --fail http://api.example.com/health
certbot --nginx -d api.example.com --redirect
certbot renew --dry-run
curl --fail https://api.example.com/health
```

## 6. Deploy the static frontend to Vercel

Clone/import the `WaffleBerry_website` repository in Vercel as a static site with no framework preset. Before publishing, set the public, non-secret URL in `js/config.js`:

```javascript
window.WAFFLEBERRY_API_BASE_URL =
    "https://api.example.com/api/v1";
```

Deploy, note the final Vercel/custom-domain origin, put that exact origin in `CORS_ORIGINS` inside `/etc/waffleberry/backend.env`, then restart:

```bash
systemctl restart waffleberry-backend
curl --fail https://api.example.com/health
```

Do not put the OpenAI key, JWT secret, database credentials, or any other secret in the frontend.

## 7. Smoke-test and back up

From the deployed frontend, verify registration/login, Legacy JSON export download, a streamed Story Guide exchange, and a streamed Persona chat. Confirm browser requests target `https://api.example.com`, CORS succeeds only for configured origins, and export exposes `Content-Disposition`.

Create the backup directory and a password file readable only by the application user:

```bash
install -d -m 700 -o waffleberry -g waffleberry /var/backups/waffleberry
sudo -u waffleberry sh -c 'printf "%s\n" "127.0.0.1:5432:waffleberry:waffleberry_app:CHANGE_ME_DATABASE_PASSWORD" > /home/waffleberry/.pgpass'
chmod 600 /home/waffleberry/.pgpass
sudo -u waffleberry crontab -e
```

Add this nightly entry (seven-day retention):

```cron
15 2 * * * pg_dump -h 127.0.0.1 -U waffleberry_app -Fc waffleberry > /var/backups/waffleberry/waffleberry-$(date +\%F).dump && find /var/backups/waffleberry -type f -name 'waffleberry-*.dump' -mtime +7 -delete
```

Test it once and confirm the dump is non-empty:

```bash
sudo -u waffleberry pg_dump -h 127.0.0.1 -U waffleberry_app -Fc waffleberry -f /var/backups/waffleberry/manual-test.dump
ls -lh /var/backups/waffleberry/manual-test.dump
```
