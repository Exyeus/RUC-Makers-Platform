"""Finalist review and award confirmation, per track.

The page shows each track's ranking with every judge's total, marks large
spreads and ties, and asks for a decision only where a tie crosses a cut line.
Generating a list freezes it as a ResultSnapshot; the round's signer signs it.
"""

import csv
import io
from decimal import Decimal

from django.core.exceptions import ValidationError

from eventyay.hackathon import workflow_services as workflow
from eventyay.hackathon.workflow_models import ResultSnapshot


SPREAD_LIMIT = Decimal('20')
AWARD_SUGGESTIONS = ['一等奖', '二等奖', '三等奖']
AWARD_CHOICES = ['一等奖', '二等奖', '三等奖', '优胜奖', '最佳人气奖', '最佳创意奖']


def judge_totals(item, row):
    names = {judge['user_id']: judge.get('name', '') for judge in item.panel}
    totals = []
    for source in row['scores']:
        total = sum(
            Decimal(str(source['values'][criterion['key']])) * criterion['weight'] / 10
            for criterion in item.rules['criteria']
        )
        totals.append({'name': names.get(source['judge_id'], ''), 'total': total.quantize(Decimal('.1'))})
    return totals


def tie_groups(rows):
    """Consecutive complete rows with the same average, as lists of indexes."""
    groups, start = [], 0
    for index in range(1, len(rows) + 1):
        if index == len(rows) or not rows[index]['complete'] or rows[index]['average'] != rows[start]['average']:
            if index - start > 1 and rows[start]['complete']:
                groups.append(list(range(start, index)))
            start = index
    return groups


def tracks_for(item, seats=0, alternates=0):
    """Rows grouped by track with spread, tie and suggested status."""
    grouped = {}
    for row in workflow.round_results(item):
        grouped.setdefault(row['track_id'], []).append(row)
    result = []
    for track_id, rows in grouped.items():
        count = min(seats, len(rows)) if seats else 0
        cuts = {cut for cut in (count, count + alternates) if 0 < cut < len(rows)} if seats else set()
        groups = tie_groups(rows)
        decide = []
        for number, group in enumerate(groups, 1):
            crosses = any(group[0] < cut <= group[-1] for cut in cuts)
            for index in group:
                rows[index]['tie'] = number
                rows[index]['decide'] = crosses
            if crosses:
                decide.append(group)
        for index, row in enumerate(rows):
            row['judges'] = judge_totals(item, row)
            totals = [judge['total'] for judge in row['judges']]
            row['spread'] = max(totals) - min(totals) if len(totals) > 1 else Decimal(0)
            row['wide'] = row['spread'] >= SPREAD_LIMIT
            if seats:
                row['suggested'] = 'finalist' if index < count else 'alternate' if index < count + alternates else 'out'
                row['cut'] = index + 1 == count
        result.append(
            {
                'id': track_id,
                'name': rows[0]['track'],
                'rows': rows,
                'decide': decide,
                'complete': all(row['complete'] for row in rows),
            }
        )
    return result


def selections_from(tracks, data):
    """Full per-track order where a tie crossing a cut line was decided on the page."""
    selections = {}
    for track in tracks:
        if not track['decide']:
            continue
        rows = list(track['rows'])
        for group in track['decide']:
            picked = []
            for index in group:
                value = str(data.get(f'tie_{rows[index]["project_id"]}', '')).strip()
                if not value.isdigit():
                    raise ValidationError(f'{track["name"]}：请为同分作品填写复核顺序。')
                picked.append(int(value))
            if sorted(picked) != list(range(1, len(group) + 1)):
                raise ValidationError(f'{track["name"]}：同分作品的复核顺序须为 1 至 {len(group)}，且不重复。')
            ordered = [rows[index] for _, index in sorted(zip(picked, group))]
            for index, row in zip(group, ordered):
                rows[index] = row
        selections[track['id']] = [row['project_id'] for row in rows]
    return selections


def suggested_awards(track):
    """一、二、三等奖 by rank, left blank where ranks are tied."""
    awards = {}
    ranks = [row['rank'] for row in track['rows']]
    for row in track['rows']:
        rank = row['rank']
        if rank and rank <= len(AWARD_SUGGESTIONS) and ranks.count(rank) == 1:
            awards[row['project_id']] = AWARD_SUGGESTIONS[rank - 1]
    return awards


def awards_from(tracks, data):
    awards = {}
    split_ties = []
    for track in tracks:
        by_tie = {}
        for row in track['rows']:
            value = str(data.get(f'award_{row["project_id"]}', '')).strip()
            if len(value) > 40:
                raise ValidationError('奖项名称不超过 40 个字。')
            if value:
                awards[row['project_id']] = value
            if row.get('tie'):
                by_tie.setdefault(row['tie'], set()).add(value)
        if any(len(values) > 1 for values in by_tie.values()):
            split_ties.append(track['name'])
    return awards, split_ties


def pending(item, kind):
    """The newest unsigned list for this round, if it is the newest version of its kind."""
    latest = ResultSnapshot.objects.filter(hackathon=item.hackathon, kind=kind).order_by('-version').first()
    if latest and latest.round_id == item.pk and not latest.signed_at:
        return latest
    return None


def snapshot_tracks(snapshot):
    grouped = {}
    for row in sorted(snapshot.manifest['entries'], key=lambda row: row.get('position') or 0):
        grouped.setdefault(row['track_id'], []).append(row)
    return [{'id': key, 'name': rows[0]['track'], 'rows': rows} for key, rows in grouped.items()]


def awards_csv(snapshot):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['赛道', '决赛名次', '奖项', '队伍ID', '队伍', '队长', '作品', '决赛均分'])
    for track in snapshot_tracks(snapshot):
        for row in track['rows']:
            writer.writerow(
                [
                    track['name'],
                    row.get('rank') or '',
                    row.get('award', ''),
                    row.get('team_code', ''),
                    row.get('team', ''),
                    row.get('captain', ''),
                    row.get('title', ''),
                    row.get('average') or '',
                ]
            )
    return '﻿' + buffer.getvalue()
