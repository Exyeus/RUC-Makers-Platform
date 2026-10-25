"""Synthetic, deterministic tests. No real participant data and no cloud services."""

import math
import unittest
from dataclasses import replace
from itertools import combinations

from eventyay.hackathon.match_core import (
    Candidate,
    Idea,
    Profile,
    common_slots,
    directed_score,
    exact_small_pack,
    generate_candidates,
    greedy_pack,
    group_reasons,
    harmonic,
    marginal_coverage,
    need_coverage,
    reciprocal_score,
    team_coverage,
    weighted_score,
)


def person(pid, **kwargs):
    defaults = dict(
        event_id='event-1',
        skills={'frontend': 0.8},
        needs={'research': 1},
        interests=frozenset({'campus'}),
        slots=frozenset({10, 11, 12}),
        goal='build',
    )
    defaults.update(kwargs)
    return Profile(pid, **defaults)


def idea(**kwargs):
    defaults = dict(
        idea_id='idea-1',
        event_id='event-1',
        interests=frozenset({'campus'}),
        role_weights={'frontend': 1, 'research': 1},
    )
    defaults.update(kwargs)
    return Idea(**defaults)


class MatchingTests(unittest.TestCase):
    def test_harmonic_penalizes_one_sided_fit(self):
        self.assertAlmostEqual(harmonic(0.9, 0.1), 0.18)
        self.assertLess(harmonic(0.9, 0.1), 0.5)

    def test_harmonic_zero_and_unknown(self):
        self.assertEqual(harmonic(0, 0), 0)
        self.assertEqual(harmonic(0, 1), 0)
        self.assertIsNone(harmonic(None, 1))

    def test_scalar_cosine_regression_and_real_goal_feature(self):
        # Mathematical reproduction of the upstream scalar-cosine issue, not upstream execution.
        self.assertEqual((1 * 5) / (math.sqrt(1**2) * math.sqrt(5**2)), 1)
        a = person('a', goal='learn')
        b = person('b', goal='learn')
        c = person('c', goal='win')
        self.assertGreater(directed_score(a, b).features['goal'], directed_score(a, c).features['goal'])

    def test_directional_skill_need_is_not_skill_similarity(self):
        a = person('a', skills={'frontend': 1}, needs={'research': 1})
        b = person('b', skills={'research': 1}, needs={'frontend': 0.5})
        c = person('c', skills={'frontend': 1})
        self.assertEqual(need_coverage(a, b), 1)
        self.assertEqual(need_coverage(a, c), 0)

    def test_missing_feature_is_masked_not_perfect_match(self):
        score = weighted_score({'a': 1, 'b': None}, {'a': 0.25, 'b': 0.75})
        self.assertEqual(score.value, 1)
        self.assertEqual(score.evidence_weight, 0.25)
        empty = weighted_score({'a': None}, {'a': 1})
        self.assertIsNone(empty.value)

    def test_no_profile_data_is_not_a_high_confidence_match(self):
        a = Profile('a', 'event-1')
        b = Profile('b', 'event-1')
        self.assertIsNone(reciprocal_score(a, b))
        self.assertIn('UNKNOWN_OR_INSUFFICIENT_GROUP_TIME', group_reasons([a, b], idea()))

    def test_invalid_numbers_rejected(self):
        for value in (float('nan'), float('inf'), -0.1, 1.1):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    person('a', skills={'frontend': value})
        with self.assertRaises(ValueError):
            person('a', needs={'research': -1})

    def test_identity_and_event_filter(self):
        a = person('a')
        self.assertIsNone(reciprocal_score(a, a))
        self.assertIsNone(reciprocal_score(a, person('b', event_id='event-2')))

    def test_blocking_is_bidirectional(self):
        a = person('a', blocked_ids=frozenset({'b'}))
        b = person('b')
        self.assertIsNone(reciprocal_score(a, b))
        self.assertIsNone(reciprocal_score(b, a))

    def test_no_eligibility_or_opt_in(self):
        self.assertIsNone(reciprocal_score(person('a'), person('b', eligible=False)))
        self.assertIsNone(reciprocal_score(person('a'), person('b', opt_in=False)))

    def test_pairwise_time_does_not_imply_group_time(self):
        group = [
            person('a', slots=frozenset({1, 2})),
            person('b', slots=frozenset({2, 3})),
            person('c', slots=frozenset({1, 3})),
        ]
        for a, b in combinations(group, 2):
            self.assertTrue(a.slots & b.slots)
        self.assertEqual(common_slots(group), frozenset())
        self.assertIn('UNKNOWN_OR_INSUFFICIENT_GROUP_TIME', group_reasons(group, idea()))

    def test_async_requires_everyone_and_project(self):
        a = person('a', slots=frozenset({1}), async_ok=True)
        b = person('b', slots=frozenset({2}), async_ok=True)
        self.assertFalse(group_reasons([a, b], idea(allow_async=True)))
        self.assertTrue(group_reasons([a, b], idea(allow_async=False)))
        self.assertTrue(group_reasons([a, replace(b, async_ok=False)], idea(allow_async=True)))

    def test_capacity_and_duplicate_member(self):
        self.assertIn('CAPACITY', group_reasons([person(str(i)) for i in range(4)], idea()))
        self.assertIn('DUPLICATE_PERSON', group_reasons([person('a'), person('a')], idea()))

    def test_locked_team_cannot_be_split(self):
        block = frozenset({'a', 'b'})
        reasons = group_reasons([person('a', team_id='t'), person('c')], idea(), locked_blocks=[block])
        self.assertIn('LOCKED_BLOCK_SPLIT', reasons)
        group = [person('a', team_id='t'), person('b', team_id='t'), person('c')]
        self.assertFalse(group_reasons(group, idea(), locked_blocks=[block]))

    def test_unprotected_roster_rejected(self):
        self.assertIn('EXISTING_ROSTER_NOT_PROTECTED', group_reasons([person('a', team_id='t'), person('b')], idea()))

    def test_merging_two_existing_teams_is_not_automatic(self):
        group = [person('a', team_id='t1'), person('b', team_id='t2')]
        reasons = group_reasons(group, idea(), locked_blocks=[frozenset({'a'}), frozenset({'b'})])
        self.assertIn('TEAM_MERGE_REQUIRES_SEPARATE_CONSENT', reasons)

    def test_recruitment_requires_owner_bounty_does_not(self):
        group = [person('b'), person('c')]
        self.assertIn('OWNER_REQUIRED', group_reasons(group, idea(owner_id='a', require_owner=True)))
        self.assertNotIn('OWNER_REQUIRED', group_reasons(group, idea(owner_id='a', require_owner=False)))

    def test_idea_must_be_acceptable_to_every_member(self):
        group = [person('a'), person('b', interests=frozenset({'finance'}))]
        self.assertIn('IDEA_INTEREST_NOT_ESTABLISHED', group_reasons(group, idea()))

    def test_marginal_complement_not_redundant_skill(self):
        a = person('a', skills={'frontend': 1})
        b = person('b', skills={'frontend': 1})
        c = person('c', skills={'research': 1})
        self.assertEqual(marginal_coverage([a], b, idea()), 0)
        self.assertEqual(marginal_coverage([a], c, idea()), 0.5)

    def test_two_beginners_do_not_sum_to_expert(self):
        self.assertEqual(
            team_coverage(
                [person('a', skills={'frontend': 0.3}), person('b', skills={'frontend': 0.3})],
                idea(role_weights={'frontend': 1}),
            ),
            0.3,
        )

    def test_generation_stable_under_input_permutation(self):
        pool = [person('a'), person('b', skills={'research': 1}), person('c')]
        self.assertEqual(generate_candidates(pool, idea()), generate_candidates(list(reversed(pool)), idea()))

    def test_duplicate_input_and_pool_limit(self):
        with self.assertRaises(ValueError):
            generate_candidates([person('a'), person('a')], idea())
        with self.assertRaises(ValueError):
            generate_candidates([person(str(i)) for i in range(31)], idea())

    def test_block_validation_and_newcomer_count(self):
        pool = [person('a', team_id='t'), person('b', team_id='t'), person('c')]
        block = frozenset({'a', 'b'})
        candidates = generate_candidates(pool, idea(), locked_blocks=[block])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].newcomer_ids, frozenset({'c'}))
        with self.assertRaises(ValueError):
            generate_candidates(pool, idea(), locked_blocks=[frozenset({'missing'})])

    def test_no_candidate_is_valid_result(self):
        self.assertEqual(generate_candidates([person('a'), person('b', eligible=False)], idea()), [])
        self.assertEqual(greedy_pack([]), [])
        self.assertEqual(exact_small_pack([]), [])

    def test_greedy_is_feasible_but_can_lose_coverage(self):
        def c(members, score):
            ids = frozenset(members)
            return Candidate(ids, ids, score, 'idea-1')

        candidates = [c(['a', 'b'], 1), c(['a', 'c'], 0.9), c(['b', 'd'], 0.9)]
        greedy = greedy_pack(candidates)
        exact = exact_small_pack(candidates)
        self.assertEqual(sum(len(c.newcomer_ids) for c in greedy), 2)
        self.assertEqual(sum(len(c.newcomer_ids) for c in exact), 4)
        seen = set()
        for candidate in exact:
            self.assertFalse(seen & candidate.members)
            seen.update(candidate.members)

    def test_quality_breaks_equal_coverage_ties(self):
        a = Candidate(frozenset({'a', 'b'}), frozenset({'a', 'b'}), 0.3, 'idea-1')
        b = Candidate(frozenset({'a', 'b'}), frozenset({'a', 'b'}), 0.8, 'idea-2')
        self.assertEqual(exact_small_pack([a, b]), [b])

    def test_exact_oracle_size_guard(self):
        c = Candidate(frozenset(str(i) for i in range(21)), frozenset({'1'}), 0.5, 'idea-1')
        with self.assertRaises(ValueError):
            exact_small_pack([c])

    def test_full_existing_group_is_not_an_actionable_new_match(self):
        pool = [person('a', team_id='t'), person('b', team_id='t')]
        self.assertEqual(generate_candidates(pool, idea(), locked_blocks=[frozenset({'a', 'b'})]), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
