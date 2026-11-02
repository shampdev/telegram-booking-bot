"""Расчёт свободного времени мастера."""
from datetime import date, datetime, timedelta

from settings import DAYS_AHEAD, SLOT_STEP_MIN, WORK_DAYS, WORK_END, WORK_START


def is_working_day(day: date) -> bool:
    return day.weekday() in WORK_DAYS


def upcoming_days(today: date) -> list[date]:
    """Рабочие дни, на которые можно записаться."""
    days = (today + timedelta(days=i) for i in range(DAYS_AHEAD))
    return [d for d in days if is_working_day(d)]


def free_slots(
    day: date,
    duration: int,
    busy: list[tuple[datetime, datetime]],
    now: datetime,
) -> list[datetime]:
    """Время начала, на которое услуга длительностью `duration` минут
    помещается в рабочий день и не пересекается с занятыми промежутками."""
    if not is_working_day(day):
        return []

    step = timedelta(minutes=SLOT_STEP_MIN)
    length = timedelta(minutes=duration)
    t = datetime.combine(day, WORK_START)
    day_end = datetime.combine(day, WORK_END)

    result = []
    while t + length <= day_end:
        end = t + length
        if t > now and all(not (b_start < end and b_end > t) for b_start, b_end in busy):
            result.append(t)
        t += step
    return result
