# Legarya Backend

Local L1 backend foundation for authentication and Rya chat.

```powershell
cd "C:\Users\Saee\Desktop\Waffleberry-new\Legarya Backend\backend"
.\.venv\Scripts\Activate.ps1
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8100
```

API documentation is available at `http://127.0.0.1:8100/docs`.
