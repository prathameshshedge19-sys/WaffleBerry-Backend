"""FastAPI application initialization and setup."""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.project import router as project_router
from app.api.v1.user import router as user_router
from app.config import get_settings
from app.db import Base, engine, ensure_schema
from app.services.ai.provider_registry import validate_ai_configuration

settings = get_settings()
validate_ai_configuration(settings)

# Import models so SQLAlchemy registers them
from app.models.user import *
from app.models.verification import *

print("Registered tables:")
print(list(Base.metadata.tables.keys()))

# Create database tables
Base.metadata.create_all(bind=engine)
ensure_schema()

# Initialize FastAPI app
app = FastAPI(
    title="Waffle Berry - Voice Cloning AI",
    description="AI platform for cloning voices and having conversations with loved ones",
    version="1.0.0",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(
    user_router,
    prefix=settings.api_v1_prefix,
    tags=["users", "voice-profiles", "conversations"]
)
app.include_router(
    project_router,
    prefix=settings.api_v1_prefix,
    tags=["projects"]
)


@app.get("/", response_class=HTMLResponse)
async def read_root():
    """Return a simple backend status page."""
    return "<h1>🎤 Waffle Berry - Backend Running</h1>"


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "ok",
        "message": "Waffle Berry backend is running",
        "version": "1.0.0"
    }


@app.get(f"{settings.api_v1_prefix}/health")
async def api_health_check():
    """API health check endpoint."""
    return {
        "status": "ok",
        "message": "API is operational",
        "features": [
            "User authentication",
            "Voice profile creation",
            "Voice sample upload",
            "Conversation management",
            "Message handling"
        ]
    }



