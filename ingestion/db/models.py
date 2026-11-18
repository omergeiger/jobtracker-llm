import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Float, DateTime, Integer, Text, JSON, ForeignKey, Index
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Source(Base):
    __tablename__ = "sources"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False, unique=True)
    type = Column(String, nullable=False)   # api | rss | scraper | email | manual
    config = Column(JSON, default=dict)
    last_fetched_at = Column(DateTime)
    fetch_errors = Column(JSON, default=list)

    job_posts = relationship("JobPost", back_populates="source")


class Company(Base):
    __tablename__ = "companies"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False, unique=True)
    domain = Column(String)                 # thin stub — enriched in a later phase

    job_posts = relationship("JobPost", back_populates="company")


class JobPost(Base):
    __tablename__ = "job_posts"

    id = Column(String, primary_key=True, default=_uuid)
    source_id = Column(String, ForeignKey("sources.id"), nullable=False)
    company_id = Column(String, ForeignKey("companies.id"))
    external_id = Column(String, nullable=False)
    title = Column(String, nullable=False)
    raw_description = Column(Text)
    apply_url = Column(String)
    extracted_fields = Column(JSON)         # structured extraction output
    posting_date = Column(DateTime)
    ingested_at = Column(DateTime, default=_now)
    expires_at = Column(DateTime)
    status = Column(String, default="new")  # new | reviewed | applied | rejected | archived | closed
    score = Column(Float)
    score_breakdown = Column(JSON)
    dedup_hash = Column(String, nullable=False, unique=True)
    global_rerank_position = Column(Integer)
    rerank_rationale = Column(Text)
    user_rating = Column(Integer)           # 1-5, human-entered; null = not rated
    feedback_tags = Column(JSON, default=list)  # reusable short reason phrases, e.g. "bad location"

    source = relationship("Source", back_populates="job_posts")
    company = relationship("Company", back_populates="job_posts")
    user_actions = relationship("UserAction", back_populates="job_post")

    __table_args__ = (
        Index("ix_job_posts_dedup_hash", "dedup_hash"),
        Index("ix_job_posts_status", "status"),
        Index("ix_job_posts_posting_date", "posting_date"),
        Index("ix_job_posts_global_rerank_position", "global_rerank_position"),
        Index("ix_job_posts_user_rating", "user_rating"),
    )


class UserAction(Base):
    __tablename__ = "user_actions"

    id = Column(String, primary_key=True, default=_uuid)
    post_id = Column(String, ForeignKey("job_posts.id"), nullable=False)
    action = Column(String, nullable=False)  # saved | applied | dismissed | noted | rated | unrated | tagged | untagged
    note = Column(Text)
    created_at = Column(DateTime, default=_now)

    job_post = relationship("JobPost", back_populates="user_actions")
