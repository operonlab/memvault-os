"""Structural invariants for the scoring Pipeline.

Guards ScoringPipeline._build_pipeline() against silent reordering or
stage deletion — the kind of change that passes type-check but breaks
ranking semantics.

Contract checked:
  1. Exactly 11 stages exist (no silent removal).
  2. Stage names match a fixed ordered contract.
  3. All three filter stages (min_score, noise_filter, pairwise_dedup)
     come after every boost/normalize stage. Moving a filter before
     a boost would drop results that the boost might have rescued.
  4. Pipeline.compile() passes given the documented initial keys —
     each stage's input_keys is satisfied by a prior stage's output_keys.

Run with: ~/.local/bin/python3 <this_file>
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path

# ── Path setup (mirrors test_intent_scoring.py) ────────────────────────────
BASE = str(Path(__file__).resolve().parents[5])
sys.path.insert(0, f"{BASE}/core")
sys.path.insert(0, f"{BASE}/libs/text-ops")

# ── Stub heavy dependencies — scoring_pipeline only uses these at call-time
# (not import-time for structural checks), but the module-level imports still
# need resolvable targets.
_STUBS = [
    "sqlalchemy",
    "sqlalchemy.ext",
    "sqlalchemy.ext.asyncio",
    "sqlalchemy.orm",
    "sdk_client",
]
for mod_name in _STUBS:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

_at = types.ModuleType("src.shared.access_tracker")
_at.compute_effective_half_life = lambda **kw: 30.0  # type: ignore[attr-defined]
sys.modules["src.shared.access_tracker"] = _at

_decay = types.ModuleType("src.shared.decay")
_decay.WEIBULL_PARAMS = {}  # type: ignore[attr-defined]
_decay.weibull_decay = lambda age, tier: 1.0  # type: ignore[attr-defined]
_decay.weibull_decay_with_half_life = lambda age, hl, tier: 1.0  # type: ignore[attr-defined]
sys.modules["src.shared.decay"] = _decay

_ss = types.ModuleType("src.shared.scoring_stages")
_ss.apply_length_normalization = lambda r, **kw: r  # type: ignore[attr-defined]
_ss.apply_min_score_filter = lambda r, **kw: r  # type: ignore[attr-defined]
_ss.apply_recency_boost = lambda r, **kw: r  # type: ignore[attr-defined]
_ss.cosine_similarity = lambda a, b: 0.0  # type: ignore[attr-defined]
sys.modules["src.shared.scoring_stages"] = _ss

_noise_mod = types.ModuleType("text_ops")
_noise_sub = types.ModuleType("text_ops.noise")

class _Verdict:
    is_noise = False

_noise_sub.check_noise = lambda text: _Verdict()  # type: ignore[attr-defined]
sys.modules["text_ops"] = _noise_mod
sys.modules["text_ops.noise"] = _noise_sub

# NOTE: unlike test_intent_scoring.py, we deliberately use the REAL
# src.shared.reactive.Pipeline here. The fake Pipeline in that sibling
# test file discards ops, which would make every structural assertion
# below a no-op. The real module is pure stdlib, so loading it is safe.
sys.modules.pop("src.shared.reactive", None)

# ── Load the scoring_pipeline module ───────────────────────────────────────
def _load(rel_path: str, module_name: str):
    spec = importlib.util.spec_from_file_location(
        module_name, f"{BASE}/core/{rel_path}"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod

_scoring = _load(
    "src/modules/memvault/scoring_pipeline.py",
    "memvault_scoring_pipeline_structure",
)


# ══════════════════════════════════════════════════════════════════════════════
# Contract
# ══════════════════════════════════════════════════════════════════════════════

EXPECTED_STAGE_ORDER: tuple[str, ...] = (
    "recency",
    "importance",
    "trust_boost",
    "feedback_boost",
    "length_norm",
    "time_decay",
    "ppr_boost",
    "semantic_boost",
    "min_score",
    "noise_filter",
    "pairwise_dedup",
)

FILTER_STAGES: frozenset[str] = frozenset({"min_score", "noise_filter", "pairwise_dedup"})


class TestPipelineStructure(unittest.TestCase):
    """Lock the 11-stage pipeline shape against silent drift."""

    def setUp(self) -> None:
        self.pipeline = _scoring.ScoringPipeline()._pipeline

    def _stage_names(self) -> list[str]:
        return [op.name for op in self.pipeline]

    def test_exactly_eleven_stages(self) -> None:
        self.assertEqual(
            len(self.pipeline),
            11,
            f"Expected 11 stages, got {len(self.pipeline)}: {self._stage_names()}",
        )

    def test_stage_names_match_contract(self) -> None:
        actual = tuple(self._stage_names())
        self.assertEqual(
            actual,
            EXPECTED_STAGE_ORDER,
            "Stage order changed. If this is intentional, update "
            "EXPECTED_STAGE_ORDER and document why in the commit message.",
        )

    def test_filters_come_after_boosts(self) -> None:
        """Filter stages must run after every non-filter stage.

        Putting a filter (e.g. min_score) before a boost (e.g. ppr_boost)
        drops items that the boost could have rescued.
        """
        names = self._stage_names()
        last_non_filter_idx = max(
            i for i, n in enumerate(names) if n not in FILTER_STAGES
        )
        first_filter_idx = min(
            (i for i, n in enumerate(names) if n in FILTER_STAGES),
            default=len(names),
        )
        self.assertLess(
            last_non_filter_idx,
            first_filter_idx,
            f"Filter stage found before a non-filter stage: {names}",
        )

    def test_compile_has_no_missing_keys(self) -> None:
        """Static key-dependency check: each op's input_keys must be
        satisfied by a prior op's output_keys (or by initial_keys).
        """
        missing = self.pipeline.compile(
            initial_keys={"results", "now", "meta", "query_embedding", "ppr_scores"}
        )
        self.assertEqual(
            missing,
            [],
            f"Pipeline.compile() reported missing key dependencies: {missing}",
        )


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
