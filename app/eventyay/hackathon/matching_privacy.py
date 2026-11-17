"""Explicit field consent shared by forms and scoring boundaries."""

MATCH_FIELDS = (
    ('skills', '已确认的角色能力'),
    ('needs', '希望伙伴贡献的角色'),
    ('interests', '匹配兴趣 / 项目方向'),
    ('slots', '共同协作时段'),
    ('goal', '参赛目标'),
    ('async_ok', '异步协作意愿'),
    ('keywords', '个人资料中的个人标签'),
    ('collaboration_style', '性格 / 协作偏好'),
)
STYLES = (
    ('discuss', '边讨论边推进'),
    ('reflect', '先独立思考再交流'),
    ('plan', '先计划再执行'),
    ('iterate', '快速试验和迭代'),
    ('direct', '直接明确的反馈'),
    ('gentle', '温和充分的反馈'),
)


def default_matching_fields():
    # Preserve previously granted matching consent; new sources require opt-in.
    return ['skills', 'needs', 'interests', 'slots', 'goal', 'async_ok']


def default_public_fields():
    return ['skills', 'interests']


def allowed_fields(record):
    raw = record.matching_fields
    return (
        set(raw) & dict(MATCH_FIELDS).keys()
        if isinstance(raw, list) and all(isinstance(x, str) for x in raw)
        else set()
    )
