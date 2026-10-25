"""Original, dependency-free matching reference. Produces ADVISORY candidates only.

Not a production enrollment service. No network, no database writes, no LLM calls.
All scalar scores are heuristics, NOT probabilities. Python >= 3.10.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from itertools import combinations
from math import isfinite


ALGORITHM_VERSION = 'ruc-reference-1.0'
FEATURE_WEIGHTS = {'interest': 0.30, 'skill_need': 0.35, 'time': 0.20, 'goal': 0.15}


def _unit(value: float, name: str) -> None:
    if isinstance(value, bool) or not isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f'{name} must be a finite number in [0, 1]')


@dataclass(frozen=True)
class Profile:
    person_id: str
    event_id: str
    # None = unknown; empty mapping = explicitly supplied no skills/needs.
    skills: Mapping[str, float] | None = None
    needs: Mapping[str, float] | None = None
    interests: frozenset[str] | None = None
    # Absolute, event-scoped 30-minute UTC slot IDs, not ambiguous weekday labels.
    slots: frozenset[int] | None = None
    goal: str | None = None
    eligible: bool = True
    opt_in: bool = True
    blocked_ids: frozenset[str] = frozenset()
    team_id: str | None = None
    async_ok: bool = False

    def __post_init__(self) -> None:
        if not self.person_id or not self.event_id:
            raise ValueError('Canonical person_id and event_id are required')
        for key, value in (self.skills or {}).items():
            _unit(value, f'skill {key}')
        for key, value in (self.needs or {}).items():
            if isinstance(value, bool) or not isfinite(value) or value < 0:
                raise ValueError(f'need weight {key} must be finite and nonnegative')
        if self.slots is not None and any(type(s) is not int for s in self.slots):
            raise ValueError('slots must contain integer UTC slot IDs')


@dataclass(frozen=True)
class Idea:
    idea_id: str
    event_id: str
    interests: frozenset[str]
    role_weights: Mapping[str, float]
    owner_id: str | None = None
    require_owner: bool = False
    allow_async: bool = False

    def __post_init__(self) -> None:
        if not self.idea_id or not self.event_id:
            raise ValueError('idea_id and event_id are required')
        if self.require_owner and not self.owner_id:
            raise ValueError('Recruitment idea requires a designated owner')
        for key, value in self.role_weights.items():
            if not isfinite(value) or value < 0:
                raise ValueError(f'role weight {key} must be finite and nonnegative')


@dataclass(frozen=True)
class FeatureScore:
    value: float | None
    evidence_weight: float  # availability of features; NOT calibrated confidence.
    features: Mapping[str, float | None]


def weighted_score(features: Mapping[str, float | None], weights: Mapping[str, float]) -> FeatureScore:
    denominator = sum(weights.values())
    if denominator <= 0 or any(not isfinite(w) or w < 0 for w in weights.values()):
        raise ValueError('Weights must be finite/nonnegative with a positive sum')
    observed = {k: v for k, v in features.items() if v is not None and weights.get(k, 0) > 0}
    for k, value in observed.items():
        _unit(value, k)
    used = sum(weights[k] for k in observed)
    score = sum(weights[k] * value for k, value in observed.items()) / used if used else None
    return FeatureScore(score, used / denominator, dict(features))


def jaccard(a: frozenset[str] | None, b: frozenset[str] | None) -> float | None:
    if a is None or b is None or not (a | b):
        return None
    return len(a & b) / len(a | b)


def need_coverage(seeker: Profile, provider: Profile) -> float | None:
    weights = seeker.needs
    if not weights or sum(weights.values()) == 0 or provider.skills is None:
        return None
    return sum(w * provider.skills.get(role, 0.0) for role, w in weights.items()) / sum(weights.values())


def time_overlap(a: Profile, b: Profile) -> float | None:
    if a.slots is None or b.slots is None:
        return None
    denominator = min(len(a.slots), len(b.slots))
    return len(a.slots & b.slots) / denominator if denominator else 0.0


def pair_reasons(a: Profile, b: Profile) -> tuple[str, ...]:
    reasons = []
    if a.person_id == b.person_id:
        reasons.append('SELF')
    if a.event_id != b.event_id:
        reasons.append('EVENT_MISMATCH')
    if not a.eligible or not b.eligible:
        reasons.append('INELIGIBLE')
    if not a.opt_in or not b.opt_in:
        reasons.append('NO_MATCHING_CONSENT')
    if a.person_id in b.blocked_ids or b.person_id in a.blocked_ids:
        reasons.append('PRIVATE_EXCLUSION')
    return tuple(reasons)


def directed_score(a: Profile, b: Profile) -> FeatureScore:
    features = {
        'interest': jaccard(a.interests, b.interests),
        'skill_need': need_coverage(a, b),
        'time': time_overlap(a, b),
        # Nominal categories, not one-dimensional cosine similarity.
        'goal': None if a.goal is None or b.goal is None else (1.0 if a.goal == b.goal else 0.5),
    }
    return weighted_score(features, FEATURE_WEIGHTS)


def harmonic(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    _unit(a, 'forward score')
    _unit(b, 'reverse score')
    return 2 * a * b / (a + b) if a + b else 0.0


def reciprocal_score(a: Profile, b: Profile) -> float | None:
    if pair_reasons(a, b):
        return None
    return harmonic(directed_score(a, b).value, directed_score(b, a).value)


def common_slots(members: Sequence[Profile]) -> frozenset[int] | None:
    if not members or any(p.slots is None for p in members):
        return None
    result = set(members[0].slots or ())
    for p in members[1:]:
        result.intersection_update(p.slots or ())
    return frozenset(result)


def team_coverage(members: Sequence[Profile], idea: Idea) -> float | None:
    if not members or not idea.role_weights or sum(idea.role_weights.values()) == 0:
        return None
    # Conservative lower bound: unreported skills are not treated as proven ability.
    return sum(
        weight * max((p.skills or {}).get(role, 0.0) for p in members) for role, weight in idea.role_weights.items()
    ) / sum(idea.role_weights.values())


def marginal_coverage(members: Sequence[Profile], candidate: Profile, idea: Idea) -> float | None:
    before = team_coverage(members, idea)
    after = team_coverage([*members, candidate], idea)
    if after is None:
        return None
    return max(0.0, after - (before or 0.0))


def group_reasons(
    members: Sequence[Profile],
    idea: Idea,
    *,
    max_size: int = 3,
    min_common_slots: int = 1,
    locked_blocks: Sequence[frozenset[str]] = (),
) -> tuple[str, ...]:
    if max_size < 2 or min_common_slots < 1:
        raise ValueError('Invalid event policy')
    ids = frozenset(p.person_id for p in members)
    reasons: set[str] = set()
    if len(ids) != len(members):
        reasons.add('DUPLICATE_PERSON')
    if not 2 <= len(members) <= max_size:
        reasons.add('CAPACITY')
    if any(p.event_id != idea.event_id for p in members):
        reasons.add('EVENT_MISMATCH')
    if idea.require_owner and idea.owner_id not in ids:
        reasons.add('OWNER_REQUIRED')
    for block in locked_blocks:
        if ids & block and not block <= ids:
            reasons.add('LOCKED_BLOCK_SPLIT')
    # Every pre-existing roster must be provided as a complete protected block.
    for team_id in {p.team_id for p in members if p.team_id is not None}:
        roster_in_group = frozenset(p.person_id for p in members if p.team_id == team_id)
        if roster_in_group not in locked_blocks:
            reasons.add('EXISTING_ROSTER_NOT_PROTECTED')
    if len({p.team_id for p in members if p.team_id is not None}) > 1:
        reasons.add('TEAM_MERGE_REQUIRES_SEPARATE_CONSENT')
    for a, b in combinations(members, 2):
        reasons.update(pair_reasons(a, b))
    slots = common_slots(members)
    async_agreed = idea.allow_async and all(p.async_ok for p in members)
    if (slots is None or len(slots) < min_common_slots) and not async_agreed:
        reasons.add('UNKNOWN_OR_INSUFFICIENT_GROUP_TIME')
    for p in members:
        # Strict default for batch proposals; discovery may show uncertain candidates separately.
        if not p.interests or not (p.interests & idea.interests):
            reasons.add('IDEA_INTEREST_NOT_ESTABLISHED')
    return tuple(sorted(reasons))


@dataclass(frozen=True)
class Candidate:
    members: frozenset[str]
    newcomer_ids: frozenset[str]
    score: float
    idea_id: str
    evidence: Mapping[str, float | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _unit(self.score, 'group score')
        if not self.members or not self.newcomer_ids <= self.members:
            raise ValueError('Invalid member IDs')


def generate_candidates(
    profiles: Sequence[Profile],
    idea: Idea,
    *,
    max_size: int = 3,
    min_common_slots: int = 1,
    min_pair_score: float = 0.25,
    locked_blocks: Sequence[frozenset[str]] = (),
    max_pool: int = 30,
) -> list[Candidate]:
    """Exhaustive small-pool reference. Production must add scoped/top-K candidate generation.

    Caller supplies COMPLETE existing team rosters and latest policy/eligibility snapshot.
    Full teams/people who did not seek recruiting must be removed by the service layer.
    This function never joins a person to a team.
    """
    if len(profiles) > max_pool:
        raise ValueError('Reference exhaustive pool too large; prefilter or use production candidate generator')
    if len({p.person_id for p in profiles}) != len(profiles):
        raise ValueError('Duplicate canonical person IDs')
    _unit(min_pair_score, 'min_pair_score')
    all_ids = {p.person_id for p in profiles}
    seen: set[str] = set()
    for block in locked_blocks:
        if not block or not block <= all_ids or seen & block:
            raise ValueError('Blocks must be complete, disjoint subsets of input profiles')
        seen.update(block)
    ordered = sorted(profiles, key=lambda p: p.person_id)
    result = []
    for size in range(2, max_size + 1):
        for group in combinations(ordered, size):
            if group_reasons(
                group, idea, max_size=max_size, min_common_slots=min_common_slots, locked_blocks=locked_blocks
            ):
                continue
            new_ids = frozenset(p.person_id for p in group if p.team_id is None)
            if not new_ids:
                continue
            pairs = [reciprocal_score(a, b) for a, b in combinations(group, 2)]
            if any(s is None or s < min_pair_score for s in pairs):
                continue
            features = {
                'reciprocal': sum(pairs) / len(pairs),
                'coverage': team_coverage(group, idea),
                'idea_interest': min(
                    len((p.interests or frozenset()) & idea.interests) / len(idea.interests) for p in group
                ),
            }
            score = weighted_score(features, {'reciprocal': 0.40, 'coverage': 0.35, 'idea_interest': 0.25})
            result.append(
                Candidate(frozenset(p.person_id for p in group), new_ids, score.value or 0.0, idea.idea_id, features)
            )
    return sorted(result, key=lambda c: (-len(c.newcomer_ids), -c.score, tuple(sorted(c.members)), c.idea_id))


def greedy_pack(candidates: Sequence[Candidate]) -> list[Candidate]:
    """Deterministic feasible baseline. No optimality guarantee."""
    used: set[str] = set()
    selected = []
    for c in sorted(candidates, key=lambda x: (-len(x.newcomer_ids), -x.score, tuple(sorted(x.members)), x.idea_id)):
        if c.newcomer_ids and not (used & c.members):
            used.update(c.members)
            selected.append(c)
    return selected


def exact_small_pack(candidates: Sequence[Candidate], *, max_people: int = 20) -> list[Candidate]:
    """Exact DP oracle for SMALL pools, lexicographic (newcomers covered, quality).

    Unmatched is allowed. Optimal ONLY within supplied candidate set and integer-scaled
    objective. No one is enrolled. Not intended to replace production CP-SAT at 150 users.
    """
    ordered = sorted(candidates, key=lambda c: (tuple(sorted(c.members)), c.idea_id, -c.score))
    ids = sorted(set().union(*(c.members for c in ordered))) if ordered else []
    if len(ids) > max_people:
        raise ValueError('Exact reference limited to small pools')
    positions = {pid: i for i, pid in enumerate(ids)}
    masks = [sum(1 << positions[pid] for pid in c.members) for c in ordered]
    containing: dict[int, list[int]] = {i: [] for i in range(len(ids))}
    for idx, c in enumerate(ordered):
        if c.newcomer_ids:
            for pid in c.members:
                containing[positions[pid]].append(idx)

    @cache
    def solve(remaining: int) -> tuple[int, int, tuple[int, ...]]:
        if not remaining:
            return 0, 0, ()
        bit = remaining & -remaining
        pos = bit.bit_length() - 1
        best = solve(remaining ^ bit)  # Leave this person unmatched.
        for idx in containing[pos]:
            mask = masks[idx]
            if mask & remaining != mask:
                continue
            count, quality, selected = solve(remaining ^ mask)
            newly = len(ordered[idx].newcomer_ids)
            option = (count + newly, quality + round(ordered[idx].score * 10000) * newly, (idx,) + selected)
            if option[:2] > best[:2]:
                best = option
        return best

    _, _, indices = solve((1 << len(ids)) - 1)
    return [ordered[i] for i in indices]
