"""review_calendar.py — Calendar page API.

Rules-driven review reminders (review_reminders.py), the daily prediction
history with grades and reflections (predictions.py), and the lessons the
prediction loop has learned — which the user can retire or restore.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import DailyPrediction, PositionReviewReminder, PredictionLesson
from ..trading.predictions import track_record
from ..trading.review_reminders import reminders_between, sync_review_reminders
from .auth_routes import get_current_user

router = APIRouter(prefix="/calendar", tags=["calendar"])

_NY_TZ = ZoneInfo("America/New_York")
_MAX_RANGE_DAYS = 92


class ReminderOut(BaseModel):
    reminder_id: UUID
    ticker: str
    kind: str
    due_date: date
    title: str
    detail: Optional[str] = None
    status: str

    class Config:
        orm_mode = True


class PredictionOut(BaseModel):
    prediction_id: UUID
    trade_date: date
    ticker: str
    direction: str
    confidence: int
    expected_move_pct: Optional[float] = None
    action: Optional[str] = None
    rationale: Optional[str] = None
    reference_close: Optional[float] = None
    close_price: Optional[float] = None
    actual_change_pct: Optional[float] = None
    market_change_pct: Optional[float] = None
    outcome: Optional[str] = None
    reflection: Optional[str] = None

    class Config:
        orm_mode = True


class TrackRecordOut(BaseModel):
    total: int
    correct: int
    high_total: int
    high_correct: int
    per_ticker: dict


class CalendarOut(BaseModel):
    today: date
    reminders: list[ReminderOut]
    predictions: list[PredictionOut]
    track_record: TrackRecordOut


class ReminderUpdate(BaseModel):
    status: str = Field(..., regex="^(pending|done|dismissed)$")


class LessonOut(BaseModel):
    lesson_id: UUID
    ticker: Optional[str] = None
    lesson: str
    source_date: date
    active: bool
    created_at: datetime

    class Config:
        orm_mode = True


class LessonUpdate(BaseModel):
    active: bool


@router.get("/events", response_model=CalendarOut)
async def calendar_events(
    start: date = Query(...),
    end: date = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if end < start or (end - start).days > _MAX_RANGE_DAYS:
        raise HTTPException(status_code=400, detail=f"Range must be 0-{_MAX_RANGE_DAYS} days")
    today = datetime.now(_NY_TZ).date()
    # Pick up positions added since the morning run; earnings come from the
    # daily worker sync (a yfinance call per ticker is too slow for a GET).
    await sync_review_reminders(db, today, user_id=current_user.user_id, include_earnings=False)
    await db.commit()

    reminders = await reminders_between(db, current_user.user_id, start, end)
    res = await db.execute(
        select(DailyPrediction)
        .where(
            DailyPrediction.user_id == current_user.user_id,
            DailyPrediction.trade_date >= start,
            DailyPrediction.trade_date <= end,
        )
        .order_by(DailyPrediction.trade_date, DailyPrediction.ticker)
    )
    stats = await track_record(db, current_user.user_id, today)
    return CalendarOut(
        today=today,
        reminders=reminders,
        predictions=res.scalars().all(),
        track_record=TrackRecordOut(
            total=stats["total"],
            correct=stats["correct"],
            high_total=stats["high_total"],
            high_correct=stats["high_correct"],
            per_ticker={k: list(v) for k, v in stats["per_ticker"].items()},
        ),
    )


@router.patch("/reminders/{reminder_id}", response_model=ReminderOut)
async def update_reminder(
    reminder_id: UUID,
    body: ReminderUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    res = await db.execute(
        select(PositionReviewReminder).where(
            PositionReviewReminder.reminder_id == reminder_id,
            PositionReviewReminder.user_id == current_user.user_id,
        )
    )
    reminder = res.scalar_one_or_none()
    if reminder is None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    reminder.status = body.status
    await db.commit()
    await db.refresh(reminder)
    return reminder


@router.get("/lessons", response_model=list[LessonOut])
async def list_lessons(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    res = await db.execute(
        select(PredictionLesson)
        .where(PredictionLesson.user_id == current_user.user_id)
        .order_by(PredictionLesson.active.desc(), PredictionLesson.created_at.desc())
        .limit(200)
    )
    return res.scalars().all()


@router.patch("/lessons/{lesson_id}", response_model=LessonOut)
async def update_lesson(
    lesson_id: UUID,
    body: LessonUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    res = await db.execute(
        select(PredictionLesson).where(
            PredictionLesson.lesson_id == lesson_id,
            PredictionLesson.user_id == current_user.user_id,
        )
    )
    lesson = res.scalar_one_or_none()
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    lesson.active = body.active
    await db.commit()
    await db.refresh(lesson)
    return lesson
