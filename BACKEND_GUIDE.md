# WaffleBerry Backend Setup

The backend is a FastAPI service that provides authentication, conversation,
and message APIs. The separate `WaffleBerry_website` repository provides the
browser frontend.

## Requirements

- Python 3.10 or newer
- SQLite for local development, or a running PostgreSQL server
- A terminal opened in this repository

## Local setup

Create and activate a virtual environment:

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

On macOS or Linux, activate it with:

```bash
source .venv/bin/activate
```

Install the dependencies:

```bash
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env`, then replace `JWT_SECRET_KEY` with a long,
random secret. The populated `.env` file is ignored by Git and must not be
committed.

```powershell
Copy-Item .env.example .env
```

The default development database is:

```env
DATABASE_URL=sqlite:///./waffle_berry.db
```

Start the development server:

```bash
python run.py
```

The API is available at `http://127.0.0.1:8000`. Useful pages:

- Health check: `http://127.0.0.1:8000/health`
- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

`run.py` enables automatic reload and is intended for local development.

## PostgreSQL

PostgreSQL must be installed and running before the backend starts. Create a
database and user, grant that user access to the database, and set the complete
connection URL in `.env`:

```env
DATABASE_URL=postgresql://waffleberry:your-password@localhost:5432/waffleberry
```

`psycopg2-binary` is included in `requirements.txt` as the PostgreSQL driver.
Do not place production credentials in source files or `.env.example`.

For a deployed environment, provide all environment variables through the
hosting platform and start the app without development reload:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Run that command from the `backend` directory. If the platform provides a
`PORT` value, substitute it for `8000`.

## Environment variables

| Variable | Purpose | Development value |
| --- | --- | --- |
| `APP_NAME` | Application display name | `Waffle Berry Backend` |
| `DEBUG` | Development debug flag | `true` |
| `DATABASE_URL` | SQLAlchemy database connection URL | `sqlite:///./waffle_berry.db` |
| `API_V1_PREFIX` | Documented API prefix | `/api/v1` |
| `JWT_SECRET_KEY` | Secret used to sign access tokens | Required; replace the placeholder |
| `JWT_ALGORITHM` | JWT signing algorithm | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Token lifetime | `30` |

## Database notes

The application creates missing tables on startup. SQLAlchemy's
`create_all()` does not migrate existing tables when models change. Future
schema changes therefore require an explicit migration process; do not delete
or recreate a database as a deployment strategy.

SQLite database files are ignored by Git. PostgreSQL is recommended for a
shared or production deployment.

## Frontend connection

Serve the separate `WaffleBerry_website` project over HTTP. Its development API
address defaults to:

```text
http://127.0.0.1:8000/api/v1
```

See that repository's README for local serving and production API URL
configuration.
