# LegaRya production deployment

The production FastAPI service runs on Hetzner from:

```text
/home/waffleberry/WaffleBerry-Backend/backend
```

The systemd unit remains named `waffleberry-backend` for operational
continuity. Its environment file is `backend/.env`, which must never be
committed.

Production requirements:

- a dedicated PostgreSQL database using the LegaRya Alembic revision chain;
- `LEGARYA_DEBUG=false`;
- `CORS_ORIGINS=https://waffleberry.app,https://www.waffleberry.app`;
- `FRONTEND_BASE_URL=https://www.waffleberry.app`;
- configured JWT, Google, mail, and AI provider credentials.

Do not run LegaRya migrations against a legacy WaffleBerry database. Preserve
and back up that database separately if rollback or a future data-import tool
may be needed.

Deploy a tested `main` commit:

```bash
cd /home/waffleberry/WaffleBerry-Backend
git pull --ff-only origin main
cd backend
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m alembic upgrade head
sudo systemctl restart waffleberry-backend
sudo systemctl status waffleberry-backend --no-pager
curl --fail http://127.0.0.1:8000/health
```

The expected health identity is:

```json
{"status":"ok","service":"legarya-backend"}
```

Nginx terminates HTTPS for `89-167-14-211.sslip.io` and proxies to
`127.0.0.1:8000`.
