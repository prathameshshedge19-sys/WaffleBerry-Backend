from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.database import get_db
from app.models.story import Story, StoryLifecycle, StoryVisibility
from app.models.user import User
from app.schemas.story import StoryCreate, StoryEditChapter, StoryGenerate, StoryPublish, StoryResponse
from app.services.authorization import legacy_role, require_legacy, require_persona_legacy
from app.services.stories import StoryEngine, get_story_provider, serialize_story

router = APIRouter(prefix="/stories", tags=["Legacy stories"])


def _story(db, story_id: str, legacy_id: int, user_id: int, *, owner=False, persona=False):
    (require_persona_legacy if persona else require_legacy)(db, user_id, legacy_id)
    story = db.scalar(select(Story).where(Story.id == story_id, Story.legacy_id == legacy_id, Story.lifecycle_state == StoryLifecycle.ACTIVE.value))
    if story is None: raise HTTPException(404, "Story not found.")
    if owner and legacy_role(db, user_id, db.get(__import__("app.models.legacy", fromlist=["Legacy"]).Legacy, legacy_id)) != "owner": raise HTTPException(403, "Only the Legacy owner can perform this Story action.")
    return story


@router.post("", response_model=StoryResponse, status_code=status.HTTP_201_CREATED)
def create_story(payload: StoryCreate, legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    legacy = require_legacy(db, user.id, legacy_id)
    story = StoryEngine(db).create(legacy, user.id, payload.title, payload.scope, payload.narrative_perspective)
    db.commit(); db.refresh(story); return serialize_story(db, story)


@router.get("", response_model=list[StoryResponse])
def list_stories(legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_legacy(db, user.id, legacy_id)
    stories = db.scalars(select(Story).where(Story.legacy_id == legacy_id, Story.lifecycle_state == StoryLifecycle.ACTIVE.value).order_by(Story.updated_at.desc(), Story.id)).all()
    return [serialize_story(db, story, include_text=False) for story in stories]


@router.get("/{story_id}", response_model=StoryResponse)
def get_story(story_id: str, legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return serialize_story(db, _story(db, story_id, legacy_id, user.id))


@router.post("/{story_id}/generate", response_model=StoryResponse)
async def generate_story(payload: StoryGenerate, story_id: str, legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db), provider=Depends(get_story_provider)):
    story = _story(db, story_id, legacy_id, user.id, owner=True)
    try:
        result = await StoryEngine(db, provider).generate(story, db.get(__import__("app.models.legacy", fromlist=["Legacy"]).Legacy, legacy_id), user.id, payload.request_key)
    except ValueError as exc:
        raise HTTPException(422, "Story generation did not pass its grounding audit.") from exc
    except Exception:
        raise HTTPException(503, "Story generation is temporarily unavailable.") from None
    return serialize_story(db, result)


@router.patch("/{story_id}/chapters/{chapter_id}", response_model=StoryResponse)
def edit_chapter(payload: StoryEditChapter, story_id: str, chapter_id: str, legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    story = _story(db, story_id, legacy_id, user.id, owner=True)
    try: result = StoryEngine(db).edit_chapter(story, chapter_id, user.id, payload.title, payload.narrative_text)
    except ValueError as exc: raise HTTPException(404, "Story chapter not found.") from exc
    return serialize_story(db, result)


@router.post("/{story_id}/publish", response_model=StoryResponse)
def publish_story(payload: StoryPublish, story_id: str, legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    story = _story(db, story_id, legacy_id, user.id, owner=True)
    story = db.scalar(select(Story).where(Story.id == story_id, Story.legacy_id == legacy_id, Story.lifecycle_state == StoryLifecycle.ACTIVE.value).with_for_update())
    if story is None: raise HTTPException(404, "Story not found.")
    if payload.published and story.current_version_id is None: raise HTTPException(409, "A generated Story version is required before publication.")
    story.visibility = StoryVisibility.PUBLISHED.value if payload.published else StoryVisibility.DRAFT.value
    db.commit(); db.refresh(story); return serialize_story(db, story)


@router.post("/{story_id}/archive", response_model=StoryResponse)
def archive_story(story_id: str, legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    story = _story(db, story_id, legacy_id, user.id, owner=True)
    story = db.scalar(select(Story).where(Story.id == story_id, Story.legacy_id == legacy_id, Story.lifecycle_state == StoryLifecycle.ACTIVE.value).with_for_update())
    if story is None: raise HTTPException(404, "Story not found.")
    story.visibility = StoryVisibility.ARCHIVED.value; story.lifecycle_state = StoryLifecycle.DELETED.value; db.commit(); db.refresh(story); return serialize_story(db, story)


@router.get("/{story_id}/provenance")
def story_provenance(story_id: str, legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return serialize_story(db, _story(db, story_id, legacy_id, user.id))
