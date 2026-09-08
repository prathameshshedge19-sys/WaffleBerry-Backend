"""Restricted operator check. Only bounded aggregate health enters the journal."""
import json
import sys

def health_snapshot():
    # Configuration/driver initialization can contain private connection details
    # in exception text too, so import only inside the sanitized call boundary.
    from app.database import SessionLocal
    from app.services.visual_health import health_summary
    return health_summary(SessionLocal)


def main():
    try:
        result = health_snapshot()
    except Exception:
        result = {"event": "visual_worker_health", "status": "error", "code": "visual_health_unavailable"}
    print(json.dumps(result), flush=True)
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
