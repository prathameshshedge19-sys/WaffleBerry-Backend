import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import distinct, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models.memory import Memory, MemoryEntityLink, MemoryStatus
from app.models.progress import BuilderActivity, DailyPrompt, PromptStatus


DOMAIN_DEFINITIONS = (
    ("identity", "Identity", 5, ("personal_detail", "place", "possession")),
    ("childhood", "Childhood", 10, ("childhood",)),
    ("relationships", "Family & Relationships", 15, ("relationship", "family_story")),
    ("education", "Education", 8, ("education",)),
    ("career", "Career", 8, ("career", "achievement")),
    ("daily_life", "Habits & Daily Life", 8, ("habit", "routine", "tradition")),
    ("preferences", "Preferences", 8, ("preference", "dislike", "opinion")),
    ("values", "Values & Beliefs", 10, ("value", "belief", "aspiration")),
    ("stories", "Stories & Experiences", 15, ("story", "life_event", "family_story", "challenge", "achievement", "tradition")),
    ("personality", "Personality & Expressions", 8, ("personality", "opinion")),
    ("reflections", "Life Lessons & Reflections", 5, ("value", "belief", "challenge", "other")),
)

PROMPT_TEMPLATES = {
    "identity": ("What is a small detail about {name} that instantly brings them to mind?", "How would someone close to {name} introduce them?") ,
    "childhood": ("What was {name} like as a child?", "Is there a school-day story about {name} that you still remember?"),
    "relationships": ("Who influenced {name} most when they were young?", "What family relationship mattered deeply to {name}?"),
    "education": ("What was {name}'s experience of school or college like?", "Was there a teacher or lesson that stayed with {name}?"),
    "career": ("What was {name}'s first job like?", "What part of their work made {name} feel proud?"),
    "daily_life": ("What did {name} enjoy doing on quiet evenings?", "What everyday routine made a place feel like home to {name}?"),
    "preferences": ("What meal did {name} make or enjoy that everyone remembers?", "What music, place, or pastime reliably lifted {name}'s mood?"),
    "values": ("What principle did {name} try to live by?", "What did {name} believe mattered most in a good life?"),
    "stories": ("What is a story about {name} that your family still tells?", "Can you remember a journey or celebration that reveals who {name} was?"),
    "personality": ("What phrase did {name} say often?", "How did {name}'s sense of humor show itself?"),
    "reflections": ("What life lesson did {name} pass on without making it feel like a lecture?", "What would {name} want the next generation to remember?"),
}


def local_date(timezone_name: str | None, now: datetime | None = None) -> date:
    try:
        zone = ZoneInfo((timezone_name or "UTC")[:64])
    except (ZoneInfoNotFoundError, ValueError):
        zone = timezone.utc
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(zone).date()


def _signature(text: str) -> str:
    tokens = re.findall(r"[\w'-]+", text.casefold())
    return " ".join(dict.fromkeys(tokens))[:180]


def _depth(memories: list[Memory]) -> int:
    unique = {(_signature(memory.canonical_text), memory.story_key or "") for memory in memories}
    if not unique:
        return 0
    distinct_count = len(unique)
    rich = sum(1 for memory in memories if len(memory.canonical_text.split()) >= 14 or memory.story_key or len(memory.entity_links) >= 3)
    variety = len({memory.story_key for memory in memories if memory.story_key})
    ratio = .48 + min(distinct_count - 1, 3) * .12 + min(rich, 2) * .08 + min(variety, 2) * .06
    return min(100, round(ratio * 100))


def legacy_progress(db: Session, legacy_id: int) -> dict:
    memories = list(db.scalars(
        select(Memory).options(selectinload(Memory.entity_links).selectinload(MemoryEntityLink.entity)).where(
            Memory.legacy_id == legacy_id, Memory.status == MemoryStatus.ACTIVE.value
        )
    ).all())
    categories = Counter(memory.category for memory in memories)
    domains = []
    total = 0.0
    for key, label, weight, mapped_categories in DOMAIN_DEFINITIONS:
        items = [memory for memory in memories if memory.category in mapped_categories]
        coverage = _depth(items)
        total += weight * coverage / 100
        domains.append({"key": key, "label": label, "weight": weight, "coverage": coverage, "memory_count": len(items)})
    ordered = sorted(domains, key=lambda item: (item["coverage"], -item["weight"], item["label"]))
    next_domain = ordered[0] if ordered else None
    return {
        "legacy_id": legacy_id,
        "percentage": round(total),
        "label": "Legacy progress",
        "domains": domains,
        "next_area": ({"key": next_domain["key"], "label": next_domain["label"], "coverage": next_domain["coverage"]} if next_domain else None),
        "active_memory_count": len(memories),
        "category_counts": dict(categories),
    }


STREAK_MILESTONES = (3, 7, 14, 30, 50, 100, 365)


def streak_summary(db: Session, legacy_id: int, today: date) -> dict:
    dates = list(db.scalars(
        select(distinct(BuilderActivity.activity_date)).where(BuilderActivity.legacy_id == legacy_id).order_by(BuilderActivity.activity_date.desc())
    ).all())
    date_set = set(dates)
    current = 0
    cursor = today
    if cursor not in date_set and cursor - timedelta(days=1) in date_set:
        cursor -= timedelta(days=1)
    while cursor in date_set:
        current += 1
        cursor -= timedelta(days=1)
    longest = 0
    run = 0
    previous = None
    for activity_date in sorted(date_set):
        run = run + 1 if previous and activity_date == previous + timedelta(days=1) else 1
        longest = max(longest, run)
        previous = activity_date
    today_activity = db.scalar(select(BuilderActivity).where(BuilderActivity.legacy_id == legacy_id, BuilderActivity.activity_date == today))
    next_milestone = next((value for value in STREAK_MILESTONES if value > current), None)
    last_date = max(date_set).isoformat() if date_set else None
    return {
        "current_streak_days": current, "longest_streak_days": longest, "today_completed": today in date_set,
        "last_meaningful_activity_date": last_date, "next_milestone": next_milestone,
        "contribution_count_today": today_activity.contribution_count if today_activity else 0,
        "last_contributor_user_id": today_activity.last_contributor_user_id if today_activity else None,
        # L8 response aliases retained for compatibility.
        "current_days": current, "longest_days": longest, "contributed_today": today in date_set, "last_contribution_date": last_date,
    }


def record_builder_activity(db: Session, *, user_id: int, legacy_id: int, activity_type: str, memory_id: int | None, activity_date: date) -> BuilderActivity:
    activity = db.scalar(select(BuilderActivity).where(BuilderActivity.legacy_id == legacy_id, BuilderActivity.activity_date == activity_date))
    first_today = activity is None
    if activity is None:
        activity = BuilderActivity(user_id=user_id, legacy_id=legacy_id, activity_type=activity_type[:32], activity_date=activity_date, memory_id=memory_id, contribution_count=1, first_contributor_user_id=user_id, last_contributor_user_id=user_id)
        db.add(activity)
    else:
        activity.contribution_count += 1
        activity.activity_type = activity_type[:32]
        activity.memory_id = memory_id
        activity.last_contributor_user_id = user_id
        activity.last_contribution_at = datetime.now(timezone.utc)
    prompt = db.scalar(select(DailyPrompt).where(
        DailyPrompt.legacy_id == legacy_id,
        DailyPrompt.shown_date == activity_date,
        DailyPrompt.status == PromptStatus.PENDING.value,
    ).order_by(DailyPrompt.id.desc()))
    if prompt:
        prompt.status = PromptStatus.ANSWERED.value
        prompt.answered_by_user_id = user_id
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        activity = db.scalar(select(BuilderActivity).where(BuilderActivity.legacy_id == legacy_id, BuilderActivity.activity_date == activity_date))
        if activity is None:
            raise
        first_today = False
        activity.contribution_count += 1
        activity.activity_type = activity_type[:32]
        activity.memory_id = memory_id
        activity.last_contributor_user_id = user_id
        activity.last_contribution_at = datetime.now(timezone.utc)
        prompt = db.scalar(select(DailyPrompt).where(
            DailyPrompt.legacy_id == legacy_id, DailyPrompt.shown_date == activity_date,
            DailyPrompt.status == PromptStatus.PENDING.value,
        ).order_by(DailyPrompt.id.desc()))
        if prompt:
            prompt.status = PromptStatus.ANSWERED.value
            prompt.answered_by_user_id = user_id
        db.commit()
    db.refresh(activity)
    activity.was_first_today = first_today
    return activity


def daily_prompt(db: Session, legacy_id: int, subject_name: str | None, shown_date: date) -> DailyPrompt:
    existing = db.scalar(select(DailyPrompt).where(
        DailyPrompt.legacy_id == legacy_id,
        DailyPrompt.shown_date == shown_date,
    ).order_by(DailyPrompt.id.desc()))
    if existing and existing.status in {PromptStatus.PENDING.value, PromptStatus.ANSWERED.value}:
        return existing
    progress = legacy_progress(db, legacy_id)
    history = list(db.scalars(select(DailyPrompt).where(DailyPrompt.legacy_id == legacy_id).order_by(DailyPrompt.id.desc()).limit(60)).all())
    used_text = {item.prompt_text for item in history}
    recently_used_categories = [item.category for item in history[:3]]
    domains = sorted(progress["domains"], key=lambda item: (item["coverage"], item["key"] in recently_used_categories, -item["weight"]))
    name = subject_name or "this person"
    chosen_category = domains[0]["key"] if domains else "stories"
    chosen_text = None
    for domain in domains:
        for template in PROMPT_TEMPLATES[domain["key"]]:
            candidate = template.format(name=name)
            if candidate not in used_text:
                chosen_category, chosen_text = domain["key"], candidate
                break
        if chosen_text:
            break
    if not chosen_text:
        template = PROMPT_TEMPLATES[chosen_category][len(history) % len(PROMPT_TEMPLATES[chosen_category])]
        chosen_text = template.format(name=name)
    prompt = DailyPrompt(legacy_id=legacy_id, prompt_text=chosen_text, category=chosen_category, shown_date=shown_date)
    db.add(prompt)
    db.commit()
    db.refresh(prompt)
    return prompt


def skip_daily_prompt(db: Session, prompt: DailyPrompt) -> None:
    if prompt.status == PromptStatus.PENDING.value:
        prompt.status = PromptStatus.SKIPPED.value
        db.commit()
