from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class DomainProgress(BaseModel):
    key: str
    label: str
    weight: int
    coverage: int
    memory_count: int


class NextArea(BaseModel):
    key: str
    label: str
    coverage: int


class LegacyProgressResponse(BaseModel):
    legacy_id: int
    percentage: int
    label: str
    domains: list[DomainProgress]
    next_area: NextArea | None
    active_memory_count: int
    category_counts: dict[str, int]


class StreakResponse(BaseModel):
    current_streak_days: int
    longest_streak_days: int
    today_completed: bool
    last_meaningful_activity_date: str | None
    next_milestone: int | None
    contribution_count_today: int
    last_contributor_user_id: int | None
    current_days: int
    longest_days: int
    contributed_today: bool
    last_contribution_date: str | None


class DailyPromptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    legacy_id: int
    prompt_text: str
    category: str
    shown_date: date
    status: str
    created_at: datetime


class BuilderJourneyResponse(BaseModel):
    role: str
    progress: LegacyProgressResponse
    streak: StreakResponse
    daily_prompt: DailyPromptResponse
