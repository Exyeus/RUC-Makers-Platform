from decimal import ROUND_HALF_UP, Decimal


CRITERIA = (
    ('innovation', '创新性', 30),
    ('implementation', '完成度与实机演示', 40),
    ('impact', '实际价值', 20),
    ('presentation', '表达', 10),
)


def weights(hackathon):
    return {name: getattr(hackathon, f'{name}_weight') for name, label, default in CRITERIA}


def weighted_total(score, hackathon):
    values = [getattr(score, name) for name in weights(hackathon)]
    if any(value is None for value in values):
        return None
    return sum(Decimal(value) * weight / 10 for value, weight in zip(values, weights(hackathon).values()))


def is_trimmed(hackathon):
    return hackathon.score_aggregation == 'trimmed' and hackathon.trim_extremes > 0


def effective_trim(hackathon, count):
    """A panel that cannot afford the window drops no score at all, rather than a partial window."""
    if not is_trimmed(hackathon) or count < 2 * hackathon.trim_extremes + 1:
        return 0
    return hackathon.trim_extremes


def aggregate(scores, hackathon):
    totals = sorted(
        total for total in (weighted_total(score, hackathon) for score in scores) if total is not None
    )
    if not totals:
        return None, 0
    trim = effective_trim(hackathon, len(totals))
    retained = totals[trim : len(totals) - trim] if trim else totals
    average = (sum(retained) / len(retained)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return average, len(retained)


def rule_description(hackathon):
    criteria = ' · '.join(f'{label} {weight}%' for (_, label, _), weight in zip(CRITERIA, weights(hackathon).values()))
    method = (
        f'去掉 {hackathon.trim_extremes} 个最高和 {hackathon.trim_extremes} 个最低评委总分后取平均；'
        f'有效评分不足 {2 * hackathon.trim_extremes + 1} 份时不去极值，改用全部有效评分取平均'
        if is_trimmed(hackathon)
        else '全部有效评委总分取平均'
    )
    return f'{criteria}。{method}；最终结果保留两位小数。'
