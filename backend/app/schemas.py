from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator


USERNAME_PATTERN = r"^[a-zA-Z0-9_.-]{3,64}$"


class RegisterRequest(BaseModel):
    """Either username or email is required; a username is derived from the email."""

    username: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=255)
    password: str = Field(min_length=6, max_length=128)
    display_name: str = Field(default="", max_length=120)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip().lower()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("邮箱格式不正确")
        return value

    @field_validator("username")
    @classmethod
    def _username(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip().lower()
        import re

        if not re.fullmatch(USERNAME_PATTERN, value):
            raise ValueError("用户名只能包含字母、数字、下划线、点或连字符，长度 3-64")
        return value

    @model_validator(mode="after")
    def _need_identifier(self) -> "RegisterRequest":
        if not self.username and not self.email:
            raise ValueError("用户名或邮箱至少填写一个")
        return self


class LoginRequest(BaseModel):
    """`identifier` accepts a username or an email. `email` kept for compatibility."""

    identifier: str | None = None
    email: str | None = None
    username: str | None = None
    password: str

    @model_validator(mode="after")
    def _resolve(self) -> "LoginRequest":
        candidate = self.identifier or self.email or self.username
        if not candidate or not candidate.strip():
            raise ValueError("请填写用户名或邮箱")
        return self

    @property
    def resolved(self) -> str:
        return (self.identifier or self.email or self.username or "").strip()


class UserOut(BaseModel):
    id: int
    username: str
    email: str | None = None
    display_name: str
    role: str
    is_active: bool = True

    model_config = {"from_attributes": True}


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class PaperOut(BaseModel):
    id: int
    title: str
    subject: str
    # "auto" = inferred from OCR text, "user" = explicitly chosen
    subject_source: str = "user"
    status: str
    error_message: str
    page_count: int
    ocr_provider: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PageOut(BaseModel):
    id: int
    page_no: int
    width: int
    height: int
    image_url: str


class RegionOut(BaseModel):
    id: int
    page_no: int
    page_id: int
    x: int
    y: int
    width: int
    height: int
    region_order: int
    label: str
    text: str
    wrong_probability: int | None
    band: str
    detection_status: str
    is_selected: bool
    is_recorded: bool


class WorkspaceOut(BaseModel):
    paper: PaperOut
    pages: list[PageOut]
    regions: list[RegionOut]
    counts: dict[str, int]


class SelectionPatch(BaseModel):
    selections: dict[int, bool]


class CommitRequest(BaseModel):
    region_ids: list[int] = Field(default_factory=list)
    idempotency_key: str | None = None


class CommitItemResult(BaseModel):
    region_id: int
    status: str
    mistake_id: int | None = None
    reason: str = ""


class CommitOut(BaseModel):
    created: int
    existing: int
    failed: int
    items: list[CommitItemResult]


class MistakeOut(BaseModel):
    id: int
    question_id: int
    label: str
    text: str
    answer_text: str
    subject: str
    difficulty: int
    knowledge_points: list[str]
    tags: list[str]
    error_reason: str
    error_detail: str
    correct_solution: str
    solution_steps: list[str]
    wrong_count: int
    wrong_probability: int | None
    status: str
    paper_title: str
    image_url: str = ""
    created_at: datetime


class MistakePageOut(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[MistakeOut]


class MistakeUpdate(BaseModel):
    subject: str | None = None
    difficulty: int | None = None
    note: str | None = None
    status: str | None = None
    tags: list[str] | None = None
    knowledge_points: list[str] | None = None
    error_reason: str | None = None
    correct_solution: str | None = None


class AnalysisRequest(BaseModel):
    region_id: int | None = None
    question_id: int | None = None
    apply: bool = True


class AnalysisOut(BaseModel):
    provider: str
    model: str
    is_fallback: bool
    error: str
    subject: str
    knowledge_points: list[str]
    difficulty: int
    error_reason: str
    error_detail: str
    correct_solution: str
    solution_steps: list[str]


class PracticeRequest(BaseModel):
    question_id: int | None = None
    mistake_id: int | None = None
    count: int = Field(default=5)
    save: bool = True

    @field_validator("count")
    @classmethod
    def _count(cls, value: int) -> int:
        if value not in (5, 10, 20):
            raise ValueError("练习题数量必须是 5、10 或 20")
        return value


class PracticeItem(BaseModel):
    text: str
    answer: str
    explanation: str
    knowledge_point: str


class PracticeOut(BaseModel):
    provider: str
    model: str
    is_fallback: bool
    error: str
    questions: list[PracticeItem]


class ReviewItemOut(BaseModel):
    plan_id: int
    mistake_id: int
    label: str
    text: str
    subject: str
    stage: int
    due_at: datetime
    knowledge_points: list[str]


class ReviewResultRequest(BaseModel):
    result: str = Field(pattern="^(pass|fail|skip)$")


class AnalyticsOut(BaseModel):
    total_mistakes: int
    new_last_7_days: int
    by_subject: dict[str, int]
    top_knowledge_points: list[dict[str, object]]
    top_wrong_count: list[dict[str, object]]
    error_reasons: dict[str, int]
    review_completion_rate: float
    repeat_wrong: list[dict[str, object]]
    weak_knowledge_points: list[dict[str, object]]


class KnowledgePointOut(BaseModel):
    id: int
    name: str
    subject: str
    question_count: int
    mistake_count: int


class TagOut(BaseModel):
    id: int
    name: str
    question_count: int


class StatusOut(BaseModel):
    app: str
    version: str
    storage_backend: str
    ai_provider: str
    ai_available: bool
    ocr: dict[str, object]
    limits: dict[str, object]
    thresholds: dict[str, int]
    review_intervals: list[int]
