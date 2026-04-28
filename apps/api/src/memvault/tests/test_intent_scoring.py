"""Adversarial tests for intent-dependent scoring weights.

Tests intent-aware configs in:
  - scoring_pipeline.py  (INTENT_SCORING_CONFIGS / scoring_config_for_intent)
  - reranker.py          (INTENT_RERANKER_WEIGHTS / reranker_weights_for_intent)
  - crag_evaluator.py    (INTENT_CRAG_WEIGHTS / crag_weights_for_intent)
  - docvault jina_rerank.py  (JinaRerankOp constructor defaults)
  - docvault graph_search.py (_OVERLAP_BOOST constant)

Run with: ~/.local/bin/python3 <this_file>
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path

# ── Stub heavy dependencies ────────────────────────────────────────────────
_STUBS = [
    "sqlalchemy",
    "sqlalchemy.ext",
    "sqlalchemy.ext.asyncio",
    "sqlalchemy.orm",
    "sqlalchemy.dialects",
    "sqlalchemy.dialects.postgresql",
    "fastapi",
    "fastapi.routing",
    "pydantic_ai",
    "sdk_client",
    "sdk_client.timeout",
    "src.shared.rerank_bridge",
    "src.shared.qdrant_search",
    "src.shared.search_types",
    "src.shared.reactive",
    "src.shared.decay",
    "src.shared.scoring_stages",
]

for mod_name in _STUBS:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

# Stub rerank_utils with real callable attributes
_rerank_utils_stub = types.ModuleType("src.shared.rerank_utils")
_rerank_utils_stub.rerank_generic = lambda *a, **kw: []  # type: ignore[attr-defined]
sys.modules["src.shared.rerank_utils"] = _rerank_utils_stub

# Stub access_tracker with a real callable
_at = types.ModuleType("src.shared.access_tracker")
_at.compute_effective_half_life = lambda **kw: 30.0  # type: ignore[attr-defined]
sys.modules["src.shared.access_tracker"] = _at

# Stub decay module with real callables
_decay = types.ModuleType("src.shared.decay")
_decay.WEIBULL_PARAMS = {}  # type: ignore[attr-defined]
_decay.weibull_decay = lambda age, tier: 1.0  # type: ignore[attr-defined]
_decay.weibull_decay_with_half_life = lambda age, hl, tier: 1.0  # type: ignore[attr-defined]
sys.modules["src.shared.decay"] = _decay

# Stub reactive Pipeline
_reactive = types.ModuleType("src.shared.reactive")

class _FakePipeline:
    def pipe(self, *ops):
        return self
    async def execute(self, ctx):
        return ctx

_reactive.Pipeline = _FakePipeline  # type: ignore[attr-defined]
sys.modules["src.shared.reactive"] = _reactive

# Stub scoring_stages
_ss = types.ModuleType("src.shared.scoring_stages")
_ss.apply_length_normalization = lambda r, **kw: r  # type: ignore[attr-defined]
_ss.apply_min_score_filter = lambda r, **kw: r  # type: ignore[attr-defined]
_ss.apply_recency_boost = lambda r, **kw: r  # type: ignore[attr-defined]
_ss.cosine_similarity = lambda a, b: 0.0  # type: ignore[attr-defined]
sys.modules["src.shared.scoring_stages"] = _ss

# Stub text_ops noise checker
_noise_mod = types.ModuleType("text_ops")
_noise_sub = types.ModuleType("text_ops.noise")

class _Verdict:
    is_noise = False

_noise_sub.check_noise = lambda text: _Verdict()  # type: ignore[attr-defined]
sys.modules["text_ops"] = _noise_mod
sys.modules["text_ops.noise"] = _noise_sub

# Stub pydantic_ai Agent
_pai = types.ModuleType("pydantic_ai")
class _FakeAgent:
    def __init__(self, **kw): pass
_pai.Agent = _FakeAgent  # type: ignore[attr-defined]
sys.modules["pydantic_ai"] = _pai

# Stub sdk_client.timeout
_sct = types.ModuleType("sdk_client.timeout")
_sct.dynamic_timeout = lambda **kw: 10  # type: ignore[attr-defined]
sys.modules["sdk_client.timeout"] = _sct

# ── Path setup ─────────────────────────────────────────────────────────────
# Resolve repo root from this file: core/src/modules/memvault/tests/<this>
# parents[5] walks up tests → memvault → modules → src → core → <repo root>
BASE = str(Path(__file__).resolve().parents[5])
sys.path.insert(0, f"{BASE}/core")
sys.path.insert(0, f"{BASE}/libs/text-ops")

# Stub sub-module imports needed by crag_evaluator
sys.modules.setdefault("src", types.ModuleType("src"))
sys.modules.setdefault("src.modules", types.ModuleType("src.modules"))

# ── Load modules under test via importlib ──────────────────────────────────
def _load(rel_path: str, module_name: str):
    spec = importlib.util.spec_from_file_location(
        module_name, f"{BASE}/core/{rel_path}"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod

# scoring_pipeline needs llm_config stub for crag_evaluator later; load order matters
_scoring = _load(
    "src/modules/memvault/scoring_pipeline.py",
    "memvault_scoring_pipeline",
)

_reranker = _load(
    "src/modules/memvault/reranker.py",
    "memvault_reranker",
)

# crag_evaluator imports from local .llm_config and .llm_models — stub them
_lc = types.ModuleType("memvault_llm_config")
_lc.get_litellm_model = lambda: "haiku"  # type: ignore[attr-defined]
sys.modules["memvault_llm_config"] = _lc

_lm = types.ModuleType("memvault_llm_models")
class _FakeCRAGOutput:
    verdict = "correct"
_lm.CRAGVerdictOutput = _FakeCRAGOutput  # type: ignore[attr-defined]
sys.modules["memvault_llm_models"] = _lm

# ── crag_evaluator: uses relative imports (from .llm_config, from .llm_models)
# Set up the package namespace so relative imports resolve correctly.
_MEMVAULT_PKG = "src.memvault"

def _ensure_pkg_chain(dotted: str):
    """Create all intermediate package stubs for a dotted module path."""
    parts = dotted.split(".")
    for i in range(1, len(parts) + 1):
        name = ".".join(parts[:i])
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)

_ensure_pkg_chain(_MEMVAULT_PKG)

# Register stubs as sub-modules of the package
_llm_config_stub = types.ModuleType(f"{_MEMVAULT_PKG}.llm_config")
_llm_config_stub.get_litellm_model = lambda: "haiku"  # type: ignore[attr-defined]
sys.modules[f"{_MEMVAULT_PKG}.llm_config"] = _llm_config_stub

_llm_models_stub = types.ModuleType(f"{_MEMVAULT_PKG}.llm_models")
_llm_models_stub.CRAGVerdictOutput = _FakeCRAGOutput  # type: ignore[attr-defined]
sys.modules[f"{_MEMVAULT_PKG}.llm_models"] = _llm_models_stub

# Also stub kg_schemas (TYPE_CHECKING import, shouldn't run but be safe)
sys.modules[f"{_MEMVAULT_PKG}.kg_schemas"] = types.ModuleType(f"{_MEMVAULT_PKG}.kg_schemas")

_crag_spec = importlib.util.spec_from_file_location(
    f"{_MEMVAULT_PKG}.crag_evaluator",
    f"{BASE}/core/src/modules/memvault/crag_evaluator.py",
    submodule_search_locations=[],
)
_crag_mod = importlib.util.module_from_spec(_crag_spec)
_crag_mod.__package__ = _MEMVAULT_PKG
sys.modules[f"{_MEMVAULT_PKG}.crag_evaluator"] = _crag_mod
try:
    _crag_spec.loader.exec_module(_crag_mod)  # type: ignore[union-attr]
    _crag_ok = True
except Exception as _crag_err:
    _crag_ok = False
    _crag_err_msg = str(_crag_err)

# ── docvault ops: jina_rerank uses absolute src.shared.rerank_utils (already stubbed)
_qdrant_mod = sys.modules["src.shared.qdrant_search"]
_qdrant_mod.hybrid_search = lambda *a, **kw: []  # type: ignore[attr-defined]

_search_types = sys.modules["src.shared.search_types"]
_search_types.SearchConfig = object  # type: ignore[attr-defined]

_DOCVAULT_PKG = "src.modules.docvault"
_DOCVAULT_OPS_PKG = f"{_DOCVAULT_PKG}.ops"
_ensure_pkg_chain(_DOCVAULT_OPS_PKG)

_jina_rerank_spec = importlib.util.spec_from_file_location(
    f"{_DOCVAULT_OPS_PKG}.jina_rerank",
    f"{BASE}/core/src/modules/docvault/ops/jina_rerank.py",
    submodule_search_locations=[],
)
_jina_mod = importlib.util.module_from_spec(_jina_rerank_spec)
_jina_mod.__package__ = _DOCVAULT_OPS_PKG
sys.modules[f"{_DOCVAULT_OPS_PKG}.jina_rerank"] = _jina_mod
try:
    _jina_rerank_spec.loader.exec_module(_jina_mod)  # type: ignore[union-attr]
    _jina_ok = True
except Exception as _jina_err:
    _jina_ok = False
    _jina_err_msg = str(_jina_err)

# ── graph_search: uses relative import from .hybrid_rrf_search
_hrrf_stub = types.ModuleType(f"{_DOCVAULT_OPS_PKG}.hybrid_rrf_search")
class _FakeHybridRRFOp:
    async def __call__(self, ctx):
        return ctx
_hrrf_stub.HybridRRFSearchOp = _FakeHybridRRFOp  # type: ignore[attr-defined]
sys.modules[f"{_DOCVAULT_OPS_PKG}.hybrid_rrf_search"] = _hrrf_stub

_graph_search_spec = importlib.util.spec_from_file_location(
    f"{_DOCVAULT_OPS_PKG}.graph_search",
    f"{BASE}/core/src/modules/docvault/ops/graph_search.py",
    submodule_search_locations=[],
)
_graph_mod = importlib.util.module_from_spec(_graph_search_spec)
_graph_mod.__package__ = _DOCVAULT_OPS_PKG
sys.modules[f"{_DOCVAULT_OPS_PKG}.graph_search"] = _graph_mod
try:
    _graph_search_spec.loader.exec_module(_graph_mod)  # type: ignore[union-attr]
    _graph_ok = True
except Exception as _graph_err:
    _graph_ok = False
    _graph_err_msg = str(_graph_err)


# ══════════════════════════════════════════════════════════════════════════════
# Test Cases
# ══════════════════════════════════════════════════════════════════════════════

EXPECTED_INTENTS = {"entity_lookup", "conceptual", "factual", "exploratory", "cross_domain"}
_TOL = 1e-9  # floating-point tolerance for sum checks


class TestCompletenessAllIntentsPresent(unittest.TestCase):
    """All 5 intents must have entries in every config dict."""

    def test_scoring_configs_completeness(self):
        missing = EXPECTED_INTENTS - set(_scoring.INTENT_SCORING_CONFIGS.keys())
        self.assertEqual(
            missing, set(),
            f"INTENT_SCORING_CONFIGS is missing intents: {missing}"
        )

    def test_reranker_weights_completeness(self):
        missing = EXPECTED_INTENTS - set(_reranker.INTENT_RERANKER_WEIGHTS.keys())
        self.assertEqual(
            missing, set(),
            f"INTENT_RERANKER_WEIGHTS is missing intents: {missing}"
        )

    def test_crag_weights_completeness(self):
        self.assertTrue(_crag_ok, f"crag_evaluator failed to load: {_crag_err_msg if not _crag_ok else ''}")
        missing = EXPECTED_INTENTS - set(_crag_mod.INTENT_CRAG_WEIGHTS.keys())
        self.assertEqual(
            missing, set(),
            f"INTENT_CRAG_WEIGHTS is missing intents: {missing}"
        )


class TestDifferentiationFromDefault(unittest.TestCase):
    """Every intent config must differ from the default in at least one weight.

    If all intents equal the default, the AttnRes feature adds zero value.
    """

    def test_scoring_configs_differ_from_default(self):
        default = _scoring.ScoringConfig()
        for intent, cfg in _scoring.INTENT_SCORING_CONFIGS.items():
            same_as_default = (
                cfg.recency_weight == default.recency_weight
                and cfg.semantic_boost == default.semantic_boost
                and cfg.trust_penalty == default.trust_penalty
                and cfg.feedback_weight == default.feedback_weight
            )
            self.assertFalse(
                same_as_default,
                f"INTENT_SCORING_CONFIGS['{intent}'] is identical to default ScoringConfig — "
                "intent-aware tuning has no effect"
            )

    def test_reranker_weights_differ_from_default(self):
        default = _reranker.RerankerWeights()
        for intent, w in _reranker.INTENT_RERANKER_WEIGHTS.items():
            same_as_default = (
                w.original == default.original and w.rerank == default.rerank
            )
            self.assertFalse(
                same_as_default,
                f"INTENT_RERANKER_WEIGHTS['{intent}'] is identical to default RerankerWeights — "
                "intent-aware tuning has no effect"
            )

    def test_crag_weights_differ_from_default(self):
        self.assertTrue(_crag_ok, f"crag_evaluator failed to load: {_crag_err_msg if not _crag_ok else ''}")
        default = _crag_mod.CRAGWeights()
        for intent, w in _crag_mod.INTENT_CRAG_WEIGHTS.items():
            same_as_default = (
                w.coverage == default.coverage
                and w.density == default.density
                and w.rules == default.rules
                and w.rerank == default.rerank
            )
            self.assertFalse(
                same_as_default,
                f"INTENT_CRAG_WEIGHTS['{intent}'] is identical to default CRAGWeights — "
                "intent-aware tuning has no effect"
            )


class TestWeightNormalization(unittest.TestCase):
    """Weights within each group must sum to 1.0 to avoid score scale drift."""

    def test_reranker_weights_sum_to_one(self):
        for intent, w in _reranker.INTENT_RERANKER_WEIGHTS.items():
            total = w.original + w.rerank
            self.assertAlmostEqual(
                total, 1.0, delta=_TOL,
                msg=f"INTENT_RERANKER_WEIGHTS['{intent}']: original({w.original}) + "
                    f"rerank({w.rerank}) = {total:.6f}, expected 1.0"
            )

    def test_reranker_default_sums_to_one(self):
        w = _reranker.RerankerWeights()
        total = w.original + w.rerank
        self.assertAlmostEqual(total, 1.0, delta=_TOL,
            msg=f"Default RerankerWeights: {total:.6f} ≠ 1.0")

    def test_crag_coverage_density_sum_to_one(self):
        self.assertTrue(_crag_ok, f"crag_evaluator failed to load: {_crag_err_msg if not _crag_ok else ''}")
        for intent, w in _crag_mod.INTENT_CRAG_WEIGHTS.items():
            total = w.coverage + w.density
            self.assertAlmostEqual(
                total, 1.0, delta=_TOL,
                msg=f"INTENT_CRAG_WEIGHTS['{intent}']: coverage({w.coverage}) + "
                    f"density({w.density}) = {total:.6f}, expected 1.0"
            )

    def test_crag_rules_rerank_sum_to_one(self):
        self.assertTrue(_crag_ok, f"crag_evaluator failed to load: {_crag_err_msg if not _crag_ok else ''}")
        for intent, w in _crag_mod.INTENT_CRAG_WEIGHTS.items():
            total = w.rules + w.rerank
            self.assertAlmostEqual(
                total, 1.0, delta=_TOL,
                msg=f"INTENT_CRAG_WEIGHTS['{intent}']: rules({w.rules}) + "
                    f"rerank({w.rerank}) = {total:.6f}, expected 1.0"
            )

    def test_crag_default_coverage_density_sum_to_one(self):
        self.assertTrue(_crag_ok, f"crag_evaluator failed to load: {_crag_err_msg if not _crag_ok else ''}")
        w = _crag_mod.CRAGWeights()
        self.assertAlmostEqual(w.coverage + w.density, 1.0, delta=_TOL,
            msg=f"Default CRAGWeights coverage+density = {w.coverage+w.density:.6f}")

    def test_crag_default_rules_rerank_sum_to_one(self):
        self.assertTrue(_crag_ok, f"crag_evaluator failed to load: {_crag_err_msg if not _crag_ok else ''}")
        w = _crag_mod.CRAGWeights()
        self.assertAlmostEqual(w.rules + w.rerank, 1.0, delta=_TOL,
            msg=f"Default CRAGWeights rules+rerank = {w.rules+w.rerank:.6f}")


class TestFallbackSafety(unittest.TestCase):
    """Unknown/garbage intents must return defaults without crashing."""

    _GARBAGE_INTENTS = [
        "",                    # empty string
        "ENTITY_LOOKUP",       # wrong case
        "does_not_exist",      # unknown key
        " entity_lookup",      # leading whitespace
        "entity_lookup ",      # trailing whitespace
        "entity lookup",       # space in key
        None,                  # None (would crash if not guarded)
        123,                   # wrong type
        "factual\x00",         # null byte injection
    ]

    def _test_factory_does_not_crash(self, factory, default_type, label):
        for bad in self._GARBAGE_INTENTS:
            with self.subTest(intent=repr(bad)):
                try:
                    result = factory(bad)
                    self.assertIsInstance(
                        result, default_type,
                        f"{label}({bad!r}) returned wrong type: {type(result)}"
                    )
                except (KeyError, TypeError, AttributeError) as exc:
                    self.fail(
                        f"{label}({bad!r}) raised {type(exc).__name__}: {exc}"
                    )

    def test_scoring_fallback(self):
        self._test_factory_does_not_crash(
            _scoring.scoring_config_for_intent,
            _scoring.ScoringConfig,
            "scoring_config_for_intent",
        )

    def test_reranker_fallback(self):
        self._test_factory_does_not_crash(
            _reranker.reranker_weights_for_intent,
            _reranker.RerankerWeights,
            "reranker_weights_for_intent",
        )

    def test_crag_fallback(self):
        self.assertTrue(_crag_ok, f"crag_evaluator failed to load: {_crag_err_msg if not _crag_ok else ''}")
        self._test_factory_does_not_crash(
            _crag_mod.crag_weights_for_intent,
            _crag_mod.CRAGWeights,
            "crag_weights_for_intent",
        )


class TestExploratoryRecencyInvariant(unittest.TestCase):
    """The 'exploratory' intent MUST have the HIGHEST recency_weight.

    Semantic: "What's been going on with X recently?" is a recency-first query.
    If any other intent equals or exceeds exploratory's recency weight, the
    intent system fails to correctly model temporal freshness preference.
    """

    def test_exploratory_has_highest_recency_weight(self):
        configs = _scoring.INTENT_SCORING_CONFIGS
        exploratory_rw = configs["exploratory"].recency_weight
        for intent, cfg in configs.items():
            if intent == "exploratory":
                continue
            self.assertLess(
                cfg.recency_weight,
                exploratory_rw,
                f"'{intent}'.recency_weight({cfg.recency_weight}) >= "
                f"'exploratory'.recency_weight({exploratory_rw}) — "
                "exploratory must have the highest recency weight"
            )

    def test_exploratory_recency_exceeds_default(self):
        default_rw = _scoring.ScoringConfig().recency_weight
        exp_rw = _scoring.INTENT_SCORING_CONFIGS["exploratory"].recency_weight
        self.assertGreater(
            exp_rw, default_rw,
            f"exploratory recency_weight({exp_rw}) should exceed "
            f"default({default_rw})"
        )


class TestSemanticBoostInvariant(unittest.TestCase):
    """entity_lookup and conceptual MUST have higher semantic_boost than exploratory.

    Semantic: Entity and conceptual queries depend heavily on semantic similarity;
    exploratory queries prioritise recency over deep semantic match.
    """

    def test_entity_lookup_semantic_boost_exceeds_exploratory(self):
        cfgs = _scoring.INTENT_SCORING_CONFIGS
        self.assertGreater(
            cfgs["entity_lookup"].semantic_boost,
            cfgs["exploratory"].semantic_boost,
            "entity_lookup.semantic_boost must exceed exploratory.semantic_boost"
        )

    def test_conceptual_semantic_boost_exceeds_exploratory(self):
        cfgs = _scoring.INTENT_SCORING_CONFIGS
        self.assertGreater(
            cfgs["conceptual"].semantic_boost,
            cfgs["exploratory"].semantic_boost,
            "conceptual.semantic_boost must exceed exploratory.semantic_boost"
        )


class TestFactualTrustPenaltyInvariant(unittest.TestCase):
    """Factual queries MUST have the HIGHEST trust_penalty.

    Semantic: Facts need trust verification; low-trust sources should be
    penalised most aggressively when answering factual questions.
    """

    def test_factual_has_highest_trust_penalty(self):
        configs = _scoring.INTENT_SCORING_CONFIGS
        factual_tp = configs["factual"].trust_penalty
        for intent, cfg in configs.items():
            if intent == "factual":
                continue
            self.assertLess(
                cfg.trust_penalty,
                factual_tp,
                f"'{intent}'.trust_penalty({cfg.trust_penalty}) >= "
                f"'factual'.trust_penalty({factual_tp}) — "
                "factual must have the highest trust_penalty"
            )


class TestTypeSafety(unittest.TestCase):
    """All factory functions must return the correct dataclass type."""

    def test_scoring_config_type(self):
        for intent in EXPECTED_INTENTS:
            result = _scoring.scoring_config_for_intent(intent)
            self.assertIsInstance(
                result, _scoring.ScoringConfig,
                f"scoring_config_for_intent('{intent}') returned {type(result)}"
            )
        # Unknown intent → still ScoringConfig
        result = _scoring.scoring_config_for_intent("__unknown__")
        self.assertIsInstance(result, _scoring.ScoringConfig)

    def test_reranker_weights_type(self):
        for intent in EXPECTED_INTENTS:
            result = _reranker.reranker_weights_for_intent(intent)
            self.assertIsInstance(
                result, _reranker.RerankerWeights,
                f"reranker_weights_for_intent('{intent}') returned {type(result)}"
            )
        result = _reranker.reranker_weights_for_intent("__unknown__")
        self.assertIsInstance(result, _reranker.RerankerWeights)

    def test_crag_weights_type(self):
        self.assertTrue(_crag_ok, f"crag_evaluator failed to load: {_crag_err_msg if not _crag_ok else ''}")
        for intent in EXPECTED_INTENTS:
            result = _crag_mod.crag_weights_for_intent(intent)
            self.assertIsInstance(
                result, _crag_mod.CRAGWeights,
                f"crag_weights_for_intent('{intent}') returned {type(result)}"
            )
        result = _crag_mod.crag_weights_for_intent("__unknown__")
        self.assertIsInstance(result, _crag_mod.CRAGWeights)


class TestDocvaultJinaRerankDefaults(unittest.TestCase):
    """JinaRerankOp constructor defaults must be original=0.2, rerank=0.8."""

    def setUp(self):
        self.assertTrue(
            _jina_ok,
            f"jina_rerank.py failed to load: {_jina_err_msg if not _jina_ok else ''}"
        )

    def test_default_weight_original(self):
        op = _jina_mod.JinaRerankOp()
        self.assertAlmostEqual(
            op._weight_original, 0.2, delta=_TOL,
            msg=f"JinaRerankOp default weight_original={op._weight_original}, expected 0.2"
        )

    def test_default_weight_rerank(self):
        op = _jina_mod.JinaRerankOp()
        self.assertAlmostEqual(
            op._weight_rerank, 0.8, delta=_TOL,
            msg=f"JinaRerankOp default weight_rerank={op._weight_rerank}, expected 0.8"
        )

    def test_defaults_sum_to_one(self):
        op = _jina_mod.JinaRerankOp()
        total = op._weight_original + op._weight_rerank
        self.assertAlmostEqual(
            total, 1.0, delta=_TOL,
            msg=f"JinaRerankOp default weights sum = {total:.6f}, expected 1.0"
        )

    def test_custom_weights_accepted(self):
        """Ensure the constructor honours custom weights (not silently clamped)."""
        op = _jina_mod.JinaRerankOp(weight_original=0.5, weight_rerank=0.5)
        self.assertAlmostEqual(op._weight_original, 0.5, delta=_TOL)
        self.assertAlmostEqual(op._weight_rerank, 0.5, delta=_TOL)


class TestDocvaultOverlapBoost(unittest.TestCase):
    """_OVERLAP_BOOST must equal 0.25 (the changed value)."""

    def setUp(self):
        self.assertTrue(
            _graph_ok,
            f"graph_search.py failed to load: {_graph_err_msg if not _graph_ok else ''}"
        )

    def test_overlap_boost_value(self):
        self.assertAlmostEqual(
            _graph_mod._OVERLAP_BOOST, 0.25, delta=_TOL,
            msg=f"_OVERLAP_BOOST = {_graph_mod._OVERLAP_BOOST}, expected 0.25"
        )

    def test_overlap_boost_positive(self):
        self.assertGreater(
            _graph_mod._OVERLAP_BOOST, 0.0,
            "_OVERLAP_BOOST must be positive (it's a boost, not a penalty)"
        )

    def test_overlap_boost_less_than_one(self):
        self.assertLess(
            _graph_mod._OVERLAP_BOOST, 1.0,
            "_OVERLAP_BOOST >= 1.0 would double the score or worse"
        )


# ── Extra adversarial: no intent maps to fallback default values exactly ──

class TestNoIntentIsJustDefaultClone(unittest.TestCase):
    """No intent config must be a zero-diff clone of the default.

    Having an intent that exactly mirrors the default wastes the dict entry
    and indicates the implementer forgot to tune that intent.
    """

    def test_no_scoring_config_is_default_clone(self):
        default = _scoring.ScoringConfig()
        for intent, cfg in _scoring.INTENT_SCORING_CONFIGS.items():
            is_clone = (
                cfg.recency_weight == default.recency_weight
                and cfg.recency_half_life == default.recency_half_life
                and cfg.semantic_boost == default.semantic_boost
                and cfg.trust_penalty == default.trust_penalty
                and cfg.feedback_weight == default.feedback_weight
                and cfg.min_score == default.min_score
                and cfg.mmr_threshold == default.mmr_threshold
                and cfg.length_anchor == default.length_anchor
            )
            self.assertFalse(
                is_clone,
                f"INTENT_SCORING_CONFIGS['{intent}'] is a verbatim clone of ScoringConfig() defaults"
            )


# ── Run ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Run with verbosity=2 so each subTest is named individually
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.TestLoader().loadTestsFromModule(
        sys.modules[__name__]
    )
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
