"""Unit tests for frontier scoring (Worker 1).

Pure-function tests over compute_frontier_score / rank_top_n —
no DB, no SQLAlchemy stack required.

Run with: ~/.local/bin/python3 core/src/modules/memvault/tests/test_frontier.py
"""

import math
import sys
import unittest
from pathlib import Path

# Resolve repo root: core/src/modules/memvault/tests/<this>
_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[5]
sys.path.insert(0, str(_REPO / "core" / "src"))

# Import the pure-function surface only — avoids loading SQLAlchemy models.
import importlib.util  # noqa: E402

_FRONTIER_PATH = _REPO / "core" / "src" / "modules" / "memvault" / "frontier.py"
_MOD_NAME = "memvault_frontier_under_test"
_spec = importlib.util.spec_from_file_location(_MOD_NAME, _FRONTIER_PATH)
frontier = importlib.util.module_from_spec(_spec)
sys.modules[_MOD_NAME] = frontier  # required for @dataclass to find the module
_spec.loader.exec_module(frontier)  # type: ignore[union-attr]

compute_frontier_score = frontier.compute_frontier_score
rank_top_n = frontier.rank_top_n
RECENCY_TAU_DAYS = frontier.RECENCY_TAU_DAYS
KNOWLEDGE_GAP_BONUS = frontier.KNOWLEDGE_GAP_BONUS


class FrontierScoreBoundaryTests(unittest.TestCase):
    """Three boundaries the plan's acceptance criteria call out."""

    # ---- Boundary 1: orphan node (PPR == 0) ----
    def test_orphan_node_yields_zero_score(self):
        """PPR=0 must short-circuit to total score 0 regardless of other signals."""
        s = compute_frontier_score(
            entity_id="e_orphan",
            entity_name="Orphan",
            ppr=0.0,
            out_degree=10,  # noisy but should be ignored
            days_since_updated=0.0,  # fresh — should not matter
            is_in_knowledge_gaps=True,  # bonus — should not matter
        )
        self.assertEqual(s.score, 0.0)
        self.assertEqual(s.ppr, 0.0)
        # Bonus collapses to neutral on the orphan branch.
        self.assertEqual(s.knowledge_gap_bonus, 1.0)

        # rank_top_n must drop zero-score rows.
        ranked = rank_top_n([s], n=5)
        self.assertEqual(ranked, [])

    # ---- Boundary 2: very stale node — recency factor decays toward zero ----
    def test_stale_node_recency_decays(self):
        """exp(-365/30) ≈ 0 — a year-stale node should score near zero."""
        fresh = compute_frontier_score(
            entity_id="e_fresh",
            entity_name="Fresh",
            ppr=1.0,
            out_degree=5,
            days_since_updated=0.0,
            is_in_knowledge_gaps=False,
        )
        stale = compute_frontier_score(
            entity_id="e_stale",
            entity_name="Stale",
            ppr=1.0,
            out_degree=5,
            days_since_updated=365.0,
            is_in_knowledge_gaps=False,
        )
        # Both nonzero (PPR > 0, out_degree > 0) but fresh dominates.
        self.assertGreater(fresh.score, 0.0)
        self.assertGreater(stale.score, 0.0)
        self.assertLess(stale.score, fresh.score / 1000.0)

        # Sanity: stale's recency factor matches the formula.
        expected_recency = math.exp(-365.0 / RECENCY_TAU_DAYS)
        expected = 1.0 * math.log(5 + 1) * expected_recency * 1.0
        self.assertAlmostEqual(stale.score, expected, places=10)

        # Top-N ordering: fresh first.
        ranked = rank_top_n([stale, fresh], n=2)
        self.assertEqual([r.entity_id for r in ranked], ["e_fresh", "e_stale"])

    # ---- Boundary 3: empty knowledge_gaps — bonus must be neutral ----
    def test_empty_knowledge_gaps_uses_neutral_bonus(self):
        """is_in_knowledge_gaps=False → bonus = 1.0, score = ppr·log(d+1)·recency."""
        s = compute_frontier_score(
            entity_id="e_neutral",
            entity_name="Neutral",
            ppr=0.5,
            out_degree=3,
            days_since_updated=10.0,
            is_in_knowledge_gaps=False,  # gaps list was empty / entity not listed
        )
        self.assertEqual(s.knowledge_gap_bonus, 1.0)
        expected = 0.5 * math.log(3 + 1) * math.exp(-10.0 / RECENCY_TAU_DAYS) * 1.0
        self.assertAlmostEqual(s.score, expected, places=10)

        # And: when gaps DO contain the entity, the bonus multiplier kicks in.
        boosted = compute_frontier_score(
            entity_id="e_boost",
            entity_name="Boost",
            ppr=0.5,
            out_degree=3,
            days_since_updated=10.0,
            is_in_knowledge_gaps=True,
        )
        self.assertEqual(boosted.knowledge_gap_bonus, KNOWLEDGE_GAP_BONUS)
        self.assertAlmostEqual(boosted.score / s.score, KNOWLEDGE_GAP_BONUS, places=10)


class FrontierRankingTests(unittest.TestCase):
    """rank_top_n behavior under mixed inputs."""

    def test_rank_drops_zeros_and_truncates(self):
        items = [
            compute_frontier_score(
                entity_id=f"e{i}",
                entity_name=f"E{i}",
                ppr=p,
                out_degree=d,
                days_since_updated=0.0,
                is_in_knowledge_gaps=False,
            )
            for i, (p, d) in enumerate([(0.0, 0), (0.1, 1), (0.5, 5), (0.9, 10), (0.3, 3)])
        ]
        top = rank_top_n(items, n=3)
        self.assertEqual(len(top), 3)
        # Descending order
        scores = [t.score for t in top]
        self.assertEqual(scores, sorted(scores, reverse=True))
        # Zero-score entry must not appear.
        self.assertNotIn("e0", [t.entity_id for t in top])

    def test_rank_n_zero_returns_empty(self):
        s = compute_frontier_score(
            entity_id="e1",
            entity_name="E1",
            ppr=1.0,
            out_degree=1,
            days_since_updated=0.0,
            is_in_knowledge_gaps=False,
        )
        self.assertEqual(rank_top_n([s], n=0), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
