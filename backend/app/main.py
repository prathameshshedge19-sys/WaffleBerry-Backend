from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.auth import router as auth_router
from app.api.routes.access_management import router as access_management_router
from app.api.routes.collaborations import router as collaboration_router
from app.api.routes.conversations import router as conversation_router
from app.api.routes.legacies import router as legacy_router
from app.api.routes.legacy_access import router as legacy_access_router
from app.api.routes.legacy_conversations import router as legacy_conversation_router
from app.api.routes.memories import router as memory_router
from app.api.routes.progress import router as progress_router
from app.api.routes.voice import router as voice_router
from app.api.routes.realtime import router as realtime_router
from app.api.routes.media_sources import router as media_sources_router
from app.api.routes.media_review import router as media_review_router
from app.api.routes.timeline import router as timeline_router
from app.config import get_settings
from app.services.realtime_runtime import lifespan


settings = get_settings()
app = FastAPI(
    lifespan=lifespan,
    title="Legarya API",
    description="Authentication, Rya chat, conversational Legacy identity, and connected editable memory intelligence.",
    version="12.0.0-l12",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router, prefix="/api/v1")
app.include_router(access_management_router, prefix="/api/v1")
app.include_router(conversation_router, prefix="/api/v1")
app.include_router(legacy_router, prefix="/api/v1")
app.include_router(memory_router, prefix="/api/v1")
app.include_router(collaboration_router, prefix="/api/v1")
app.include_router(legacy_access_router, prefix="/api/v1")
app.include_router(legacy_conversation_router, prefix="/api/v1")
app.include_router(progress_router, prefix="/api/v1")
app.include_router(voice_router, prefix="/api/v1")
app.include_router(realtime_router, prefix="/api/v1")
app.include_router(media_sources_router, prefix="/api/v1")
app.include_router(media_review_router, prefix="/api/v1")
app.include_router(timeline_router, prefix="/api/v1")


@app.get("/health")
def health():
    return {"status": "ok", "service": "legarya-backend"}

# L13 read-only Memory Dashboard representation.
from app.api.routes.personality import router as personality_dashboard_router
app.include_router(personality_dashboard_router)
