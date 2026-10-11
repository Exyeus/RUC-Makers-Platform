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
    return sum(Decimal(getattr(score, name)) * weight / 10 for name, weight in weights(hackathon).items())


def aggregate(scores, hackathon):
    totals = sorted(weighted_total(score, hackathon) for score in scores)
    trim = hackathon.trim_extremes if hackathon.score_aggregation == 'trimmed' else 0
    if len(totals) <= 2 * trim:
        return None, 0
    retained = totals[trim : len(totals) - trim] if trim else totals
    average = (sum(retained) / len(retained)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return average, len(retained)


def rule_description(hackathon):
    criteria = ' · '.join(f'{label} {weight}%' for (_, label, _), weight in zip(CRITERIA, weights(hackathon).values()))
    method = (
        f'去掉 {hackathon.trim_extremes} 个最高和 {hackathon.trim_extremes} 个最低评委总分后取平均'
        if hackathon.score_aggregation == 'trimmed'
        else '全部有效评委总分取平均'
    )
    return f'{criteria}。{method}；最终结果保留两位小数。'
