from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from eventyay.hackathon.models import ScheduleItem


def attendance_hours(hackathon) -> list[tuple[str, list[tuple[str, str]]]]:
    zone = ZoneInfo(hackathon.event.timezone)
    first = hackathon.event.date_from.astimezone(zone).date()
    days = []
    for offset in range(2):
        day = first + timedelta(days=offset)
        hours = []
        for hour in range(24):
            instant = datetime.combine(day, time(hour), tzinfo=zone)
            if instant < hackathon.event.date_from:
                continue
            if hackathon.event.date_to and instant + timedelta(hours=1) > hackathon.event.date_to:
                continue
            hours.append((str(int(instant.timestamp()) // 3600), f'{hour:02}:00–{hour + 1:02}:00'))
        days.append((f'{day:%m 月 %d 日}', hours))
    return days


def available_half_hours(hackathon, unavailable_hours: list[int]) -> list[int]:
    unavailable = set(unavailable_hours)
    return [
        int(hour) * 2 + half
        for _, hours in attendance_hours(hackathon)
        for hour, _ in hours
        if int(hour) not in unavailable
        for half in range(2)
    ]


def absence_summary(hackathon, hours, notes: str = '') -> str:
    zone = ZoneInfo(hackathon.event.timezone)
    labels = []
    if isinstance(hours, list):
        for hour in hours:
            if type(hour) is not int:
                continue
            try:
                start = datetime.fromtimestamp(hour * 3600, zone)
                end = start + timedelta(hours=1)
            except (ValueError, OverflowError, OSError):
                continue
            labels.append(f'{start:%m-%d %H:%M}–{end:%H:%M}')
    if notes:
        labels.append(notes)
    return '；'.join(labels)


def schedule_heatmap(hackathon) -> list[dict]:
    zone = ZoneInfo(hackathon.event.timezone)
    items = list(ScheduleItem.objects.filter(hackathon=hackathon))
    deadlines = [
        (hackathon.registration_deadline, '报名截止'),
        (hackathon.team_freeze_at, '队伍冻结'),
        (hackathon.soft_deadline, '建议提交'),
        (hackathon.submission_deadline, '作品提交截止'),
        (hackathon.freeze_at, '材料最终冻结'),
    ]
    days = {hackathon.event.date_from.astimezone(zone).date()}
    end = (hackathon.event.date_to or hackathon.event.date_from).astimezone(zone).date()
    # Keep long-running camps compact; include every scheduled day and deadline.
    if (end - min(days)).days <= 14:
        days.update(min(days) + timedelta(days=i) for i in range((end - min(days)).days + 1))
    for item in items:
        start_day = item.starts_at.astimezone(zone).date()
        end_day = item.ends_at.astimezone(zone).date()
        days.update(start_day + timedelta(days=i) for i in range(min((end_day - start_day).days + 1, 15)))
    days.update(instant.astimezone(zone).date() for instant, _ in deadlines)
    result = []
    for day in sorted(days):
        hours = []
        for hour in range(24):
            instant = datetime.combine(day, time(hour), tzinfo=zone)
            until = instant + timedelta(hours=1)
            events = [
                f'{item.starts_at.astimezone(zone):%H:%M}–{item.ends_at.astimezone(zone):%H:%M} {item.title}'
                + (f' · {item.location}' if item.location else '')
                for item in items
                if item.starts_at < until and item.ends_at > instant
            ]
            due = [f'{at.astimezone(zone):%H:%M} {label}' for at, label in deadlines if instant <= at < until]
            label = f'{instant:%m-%d %H}:00 · ' + '；'.join(due + events or ['暂无安排'])
            hours.append({'hour': hour, 'label': label, 'level': 'deadline' if due else 'event' if events else 'empty'})
        result.append({'date': day, 'hours': hours})
    return result
