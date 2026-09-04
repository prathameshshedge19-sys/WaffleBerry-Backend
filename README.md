# LegaRya Backend

FastAPI backend for LegaRya. The repository retains the existing WaffleBerry
Hetzner service name and installation paths so the current production pipeline
can continue to manage the service during the product transition.

Production must use a dedicated PostgreSQL database initialized by the LegaRya
Alembic chain. Do not run these migrations against the legacy WaffleBerry
database because the two repositories have unrelated revision histories.

From `backend/`:

```powershell
python -m alembic upgrade head
python -m pytest -q
```
