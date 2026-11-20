"""Time-aware helpers for the Swiss pages: key deadlines and the event timeline.

Deadlines and schedule share one source of truth with the backend locks: the
hackathon's own datetime fields and the published schedule.
"""

from zoneinfo import ZoneInfo

from django import template
from django.utils import timezone

from eventyay.hackathon.models import ScheduleItem


register = template.Library()


def key_deadlines(hackathon):
    return [
        (hackathon.registration_deadline, '报名截止'),
        (hackathon.team_freeze_at, '队伍、队长与赛道冻结'),
        (hackathon.submission_deadline, '最终作品提交截止'),
    ]


def remaining(delta):
    """Server-rendered countdown text, the same format swiss.js keeps updating."""
    seconds = max(0, int(delta.total_seconds()))
    hours, minutes, seconds = seconds // 3600, seconds % 3600 // 60, seconds % 60
    if hours >= 100:
        return {'main': f'{hours // 24} 天', 'tail': f' {hours % 24:02d} 时'}
    return {'main': f'{hours:02d}:{minutes:02d}', 'tail': f':{seconds:02d}'}


@register.simple_tag
def sw_next_deadline(hackathon):
    """The next deadline still ahead; after the last one, that last one marked passed."""
    now = timezone.now()
    zone = ZoneInfo(hackathon.event.timezone)
    ordered = sorted(key_deadlines(hackathon), key=lambda item: item[0])
    for at, label in ordered:
        if at > now:
            return {'at': at.astimezone(zone), 'label': label, 'passed': False, **remaining(at - now)}
    at, label = ordered[-1]
    return {'at': at.astimezone(zone), 'label': label, 'passed': True}


@register.simple_tag
def sw_deadlines(hackathon):
    now = timezone.now()
    zone = ZoneInfo(hackathon.event.timezone)
    return [
        {'at': at.astimezone(zone), 'label': label, 'passed': at <= now}
        for at, label in sorted(key_deadlines(hackathon), key=lambda item: item[0])
    ]


@register.simple_tag
def sw_milestones(hackathon, limit=8, items=None):
    """Published schedule and key deadlines in time order, each with a state.

    State is one of past / now / next / future. Long schedules keep the most
    recent past entry for context and fill the rest with what is ahead.
    ``items`` lets a page that already fetched the published schedule reuse it.
    """
    now = timezone.now()
    zone = ZoneInfo(hackathon.event.timezone)
    entries = [
        {
            'at': item.starts_at.astimezone(zone),
            'ends': item.ends_at.astimezone(zone),
            'title': item.title,
            'where': item.location,
            'deadline': False,
        }
        for item in (ScheduleItem.published(hackathon) if items is None else items)
    ]
    entries += [
        {'at': at.astimezone(zone), 'ends': None, 'title': label, 'where': '平台自动执行', 'deadline': True}
        for at, label in key_deadlines(hackathon)
    ]
    entries.sort(key=lambda entry: entry['at'])
    upcoming_marked = False
    for entry in entries:
        end = entry['ends'] or entry['at']
        if entry['at'] <= now <= end and entry['ends']:
            entry['state'] = 'now'
        elif end < now:
            entry['state'] = 'past'
        elif not upcoming_marked:
            entry['state'] = 'next'
            upcoming_marked = True
        else:
            entry['state'] = 'future'
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 8
    if len(entries) <= limit:
        return entries
    first_open = next((i for i, entry in enumerate(entries) if entry['state'] != 'past'), len(entries))
    start = max(0, min(first_open - 1, len(entries) - limit))
    return entries[start : start + limit]
