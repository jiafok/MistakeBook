from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, utcnow


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Login identifier. Users may register with a username, an email, or both.
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    settings_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    papers: Mapped[list["Paper"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    mistakes: Mapped[list["Mistake"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )


class Paper(Base, TimestampMixin):
    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="未命名试卷")
    subject: Mapped[str] = mapped_column(String(64), nullable=False, default="未分类")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ocr_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    source_names: Mapped[str] = mapped_column(Text, nullable=False, default="")

    owner: Mapped["User"] = relationship(back_populates="papers")
    pages: Mapped[list["PaperPage"]] = relationship(
        back_populates="paper", cascade="all, delete-orphan", order_by="PaperPage.page_no"
    )
    sections: Mapped[list["Section"]] = relationship(
        back_populates="paper", cascade="all, delete-orphan", order_by="Section.order_index"
    )
    regions: Mapped[list["QuestionRegion"]] = relationship(
        back_populates="paper", cascade="all, delete-orphan"
    )


class PaperPage(Base, TimestampMixin):
    __tablename__ = "paper_pages"
    __table_args__ = (UniqueConstraint("paper_id", "page_no", name="uq_paper_page"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    render_dpi: Mapped[int] = mapped_column(Integer, nullable=False, default=150)
    ocr_text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    paper: Mapped["Paper"] = relationship(back_populates="pages")
    regions: Mapped[list["QuestionRegion"]] = relationship(back_populates="page")


class Section(Base, TimestampMixin):
    """Paper -> Section -> Question -> SubQuestion hierarchy."""

    __tablename__ = "sections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    paper: Mapped["Paper"] = relationship(back_populates="sections")
    questions: Mapped[list["Question"]] = relationship(
        back_populates="section", cascade="all, delete-orphan", order_by="Question.order_index"
    )


class Question(Base, TimestampMixin):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("sections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    parent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    label: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    answer_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    subject: Mapped[str] = mapped_column(String(64), nullable=False, default="未分类", index=True)
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    is_practice: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    section: Mapped[Optional["Section"]] = relationship(back_populates="questions")
    children: Mapped[list["Question"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan", order_by="Question.order_index"
    )
    parent: Mapped[Optional["Question"]] = relationship(
        back_populates="children", remote_side="Question.id"
    )
    region: Mapped[Optional["QuestionRegion"]] = relationship(
        back_populates="question", uselist=False
    )
    tags: Mapped[list["Tag"]] = relationship(
        secondary="question_tags", back_populates="questions", lazy="selectin"
    )
    knowledge_points: Mapped[list["KnowledgePoint"]] = relationship(
        secondary="question_knowledge_points", back_populates="questions", lazy="selectin"
    )


class Tag(Base, TimestampMixin):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_tag_user_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)

    questions: Mapped[list["Question"]] = relationship(
        secondary="question_tags", back_populates="tags"
    )


class QuestionTag(Base):
    __tablename__ = "question_tags"
    __table_args__ = (UniqueConstraint("question_id", "tag_id", name="uq_question_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), nullable=False, index=True
    )


class QuestionRegion(Base, TimestampMixin):
    """Geometry + recommendation signal. Never the authoritative 'is recorded' fact."""

    __tablename__ = "question_regions"
    __table_args__ = (
        Index("ix_region_paper_page_order", "paper_id", "page_no", "region_order"),
        Index("ix_region_paper_band", "paper_id", "band"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_id: Mapped[int] = mapped_column(
        ForeignKey("paper_pages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    question_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("questions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    x: Mapped[int] = mapped_column(Integer, nullable=False)
    y: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    region_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wrong_probability: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    band: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    detection_status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")

    paper: Mapped["Paper"] = relationship(back_populates="regions")
    page: Mapped["PaperPage"] = relationship(back_populates="regions")
    question: Mapped[Optional["Question"]] = relationship(back_populates="region")
    selections: Mapped[list["RegionSelection"]] = relationship(
        back_populates="region", cascade="all, delete-orphan"
    )


class RegionSelection(Base, TimestampMixin):
    """Per-user draft selection. Not the错题本 fact."""

    __tablename__ = "question_region_selections"
    __table_args__ = (UniqueConstraint("region_id", "user_id", name="uq_region_selection"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region_id: Mapped[int] = mapped_column(
        ForeignKey("question_regions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    region: Mapped["QuestionRegion"] = relationship(back_populates="selections")


class Mistake(Base, TimestampMixin):
    __tablename__ = "mistakes"
    __table_args__ = (
        UniqueConstraint("user_id", "question_id", name="uq_mistake_user_question"),
        Index("ix_mistake_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    paper_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("papers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    region_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("question_regions.id", ondelete="SET NULL"), nullable=True
    )
    question_image_key: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    selection_source: Mapped[str] = mapped_column(String(32), nullable=False, default="user_explicit")
    wrong_probability_snapshot: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    wrong_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")

    owner: Mapped["User"] = relationship(back_populates="mistakes")
    question: Mapped["Question"] = relationship()
    reviews: Mapped[list["ReviewPlan"]] = relationship(
        back_populates="mistake", cascade="all, delete-orphan"
    )


class KnowledgePoint(Base, TimestampMixin):
    __tablename__ = "knowledge_points"
    __table_args__ = (UniqueConstraint("user_id", "subject", "name", name="uq_kp"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    subject: Mapped[str] = mapped_column(String(64), nullable=False, default="未分类")

    questions: Mapped[list["Question"]] = relationship(
        secondary="question_knowledge_points", back_populates="knowledge_points"
    )


class QuestionKnowledgePoint(Base):
    __tablename__ = "question_knowledge_points"
    __table_args__ = (UniqueConstraint("question_id", "knowledge_point_id", name="uq_q_kp"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    knowledge_point_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_points.id", ondelete="CASCADE"), nullable=False, index=True
    )


class AiAnalysis(Base, TimestampMixin):
    __tablename__ = "ai_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    subject: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    knowledge_points_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    error_reason: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    error_detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    correct_solution: Mapped[str] = mapped_column(Text, nullable=False, default="")
    solution_steps_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    raw_response: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ReviewPlan(Base, TimestampMixin):
    __tablename__ = "review_plans"
    __table_args__ = (Index("ix_review_due", "user_id", "status", "due_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mistake_id: Mapped[int] = mapped_column(
        ForeignKey("mistakes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    last_result: Mapped[str] = mapped_column(String(16), nullable=False, default="")

    mistake: Mapped["Mistake"] = relationship(back_populates="reviews")
    records: Mapped[list["ReviewRecord"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )


class ReviewRecord(Base, TimestampMixin):
    __tablename__ = "review_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("review_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    result: Mapped[str] = mapped_column(String(16), nullable=False, default="pass")
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    plan: Mapped["ReviewPlan"] = relationship(back_populates="records")


class PracticeQuestion(Base, TimestampMixin):
    __tablename__ = "practice_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    source_question_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("questions.id", ondelete="SET NULL"), nullable=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    knowledge_point: Mapped[str] = mapped_column(String(128), nullable=False, default="")


class Attachment(Base, TimestampMixin):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ref_type: Mapped[str] = mapped_column(String(32), nullable=False, default="question")
    ref_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SystemSetting(Base, TimestampMixin):
    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class UsageStat(Base):
    """Lightweight counters used by analytics."""

    __tablename__ = "usage_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    value: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
