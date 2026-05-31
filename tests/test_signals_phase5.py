"""Phase 5 tests — measured signals + demo product mode removed.

What these tests cover:

1. ``_measure_signals_from_turns`` derives ``support`` /
   ``disagreement_index`` / ``margin_top1_top2`` from real specialist
   outputs (claim clustering + confidence aggregation).
2. ``signals_after_round`` uses the measured signals when ``turn_results``
   are supplied, and falls back to the legacy heuristic when they're not.
3. Same-cluster specialists produce low disagreement; split-cluster
   specialists produce high disagreement.
4. ``DECISION_ROOM_EXECUTOR=demo`` is rejected by ``build_runtime_from_env``
   (Phase 5 removed the product-mode coupling).
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Any

from decision_room.orchestration.room_executor import (
    SynthesisOutput,
    _cluster_claims_by_jaccard,
    _claim_tokens,
    _measure_signals_from_turns,
    signals_after_round,
)
from decision_room.runtime.room_models import RoomSnapshot


@dataclass
class _FakeArgument:
    claim: str
    confidence: float
    evidence: list[str]
    target_claim_ref: str = ""
    title: str = ""
    text: str = ""


@dataclass
class _FakeTurn:
    output: _FakeArgument


def _synthesis_output(
    *,
    agreement: list[str] | None = None,
    disagreement: list[str] | None = None,
    open_questions: list[str] | None = None,
) -> SynthesisOutput:
    return SynthesisOutput(
        title="t",
        text="x",
        agreement=agreement or [],
        disagreement=disagreement or [],
        open_questions=open_questions or [],
        decision_candidate="candidate",
        action_item_draft=["do it"],
        conclusion_type="follow_up_required",
        conclusion_reason="reason",
        should_end_meeting=False,
    )


class ClaimTokenizationTests(unittest.TestCase):
    def test_tokens_lowercased_and_stopwords_removed(self) -> None:
        tokens = _claim_tokens("Adopt the phased rollout for cohort A!")
        # Lowercased + punctuation stripped + stopwords removed
        self.assertIn("adopt", tokens)
        self.assertIn("phased", tokens)
        self.assertIn("rollout", tokens)
        self.assertIn("cohort", tokens)
        self.assertNotIn("the", tokens)
        self.assertNotIn("for", tokens)

    def test_empty_claim_returns_empty_set(self) -> None:
        self.assertEqual(_claim_tokens(""), set())


class JaccardClusteringTests(unittest.TestCase):
    def test_identical_claims_cluster_together(self) -> None:
        sets = [
            _claim_tokens("Adopt the phased rollout"),
            _claim_tokens("Adopt the phased rollout"),
        ]
        clusters = _cluster_claims_by_jaccard(sets)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(sorted(clusters[0]), [0, 1])

    def test_disjoint_claims_form_separate_clusters(self) -> None:
        sets = [
            _claim_tokens("Adopt phased cohort rollout"),
            _claim_tokens("Halt deployment until validator finishes"),
        ]
        clusters = _cluster_claims_by_jaccard(sets)
        self.assertEqual(len(clusters), 2)

    def test_threshold_grouping(self) -> None:
        # Two claims share 2 of 3 distinctive tokens → Jaccard 2/4 = 0.5
        sets = [
            _claim_tokens("phased cohort rollout"),
            _claim_tokens("phased cohort plan"),
        ]
        clusters = _cluster_claims_by_jaccard(sets, threshold=0.5)
        self.assertEqual(len(clusters), 1)


class MeasureSignalsTests(unittest.TestCase):
    def test_returns_none_for_empty_turns(self) -> None:
        self.assertIsNone(_measure_signals_from_turns([]))

    def test_single_cluster_high_agreement(self) -> None:
        turns = [
            _FakeTurn(
                _FakeArgument(
                    claim="Adopt the phased cohort rollout for cohort A",
                    confidence=0.82,
                    evidence=["e"],
                )
            ),
            _FakeTurn(
                _FakeArgument(
                    claim="Adopt the phased cohort rollout starting with cohort A",
                    confidence=0.78,
                    evidence=["e"],
                )
            ),
        ]
        measurements = _measure_signals_from_turns(turns)
        self.assertIsNotNone(measurements)
        self.assertAlmostEqual(measurements["mean_confidence"], 0.80, places=2)
        # Single-cluster round: low disagreement, zero margin
        self.assertEqual(measurements["disagreement_index"], 0.15)
        self.assertEqual(measurements["margin_top1_top2"], 0.0)

    def test_two_clusters_produce_disagreement(self) -> None:
        turns = [
            _FakeTurn(
                _FakeArgument(
                    claim="Adopt phased rollout",
                    confidence=0.85,
                    evidence=["e"],
                )
            ),
            _FakeTurn(
                _FakeArgument(
                    claim="Halt deployment until validator finishes",
                    confidence=0.60,
                    evidence=["e"],
                )
            ),
        ]
        measurements = _measure_signals_from_turns(turns)
        self.assertIsNotNone(measurements)
        # Two clusters → disagreement index = 1 - 1/2 = 0.5
        self.assertAlmostEqual(measurements["disagreement_index"], 0.5, places=2)
        # Top cluster mean = 0.85, second = 0.60 → margin = 0.25
        self.assertAlmostEqual(measurements["margin_top1_top2"], 0.25, places=2)


class SignalsAfterRoundIntegrationTests(unittest.TestCase):
    def _snapshot(self) -> RoomSnapshot:
        return RoomSnapshot(
            room_id="room_test",
            current_focus="focus",
            constraints=[],
        )

    def test_legacy_heuristic_used_when_turns_omitted(self) -> None:
        signals = signals_after_round(
            snapshot=self._snapshot(),
            round_index=2,
            open_questions=[],
            synthesis_output=_synthesis_output(agreement=["x"]),
        )
        # Heuristic path: support is the snapshot-derived 0.56 base plus
        # transcript-depth boost. Just confirm it sits in the legacy range.
        self.assertGreater(signals.support, 0.5)
        self.assertLess(signals.support, 0.97)

    def test_measured_path_uses_turn_confidence_for_support(self) -> None:
        turns = [
            _FakeTurn(
                _FakeArgument(
                    claim="Adopt phased rollout",
                    confidence=0.92,
                    evidence=["e"],
                )
            ),
            _FakeTurn(
                _FakeArgument(
                    claim="Adopt phased cohort rollout",
                    confidence=0.88,
                    evidence=["e"],
                )
            ),
        ]
        signals = signals_after_round(
            snapshot=self._snapshot(),
            round_index=2,
            open_questions=[],
            synthesis_output=_synthesis_output(agreement=["x"]),
            turn_results=turns,
        )
        # Measured mean confidence is 0.90; with agreement_boost (+0.03)
        # and action_item_boost (+0.04) support sits near 0.97 cap.
        self.assertGreaterEqual(signals.support, 0.95)

    def test_split_cluster_round_raises_disagreement_index(self) -> None:
        agreeing = [
            _FakeTurn(
                _FakeArgument(
                    claim="Adopt phased cohort rollout",
                    confidence=0.85,
                    evidence=["e"],
                )
            ),
            _FakeTurn(
                _FakeArgument(
                    claim="Adopt phased rollout for cohort A",
                    confidence=0.80,
                    evidence=["e"],
                )
            ),
        ]
        disagreeing = [
            _FakeTurn(
                _FakeArgument(
                    claim="Adopt phased rollout",
                    confidence=0.85,
                    evidence=["e"],
                )
            ),
            _FakeTurn(
                _FakeArgument(
                    claim="Halt deployment until validator finishes",
                    confidence=0.60,
                    evidence=["e"],
                )
            ),
        ]
        snapshot = self._snapshot()
        synth = _synthesis_output()
        agreeing_signals = signals_after_round(
            snapshot=snapshot,
            round_index=2,
            open_questions=[],
            synthesis_output=synth,
            turn_results=agreeing,
        )
        disagreeing_signals = signals_after_round(
            snapshot=snapshot,
            round_index=2,
            open_questions=[],
            synthesis_output=synth,
            turn_results=disagreeing,
        )
        self.assertGreater(
            disagreeing_signals.disagreement_index,
            agreeing_signals.disagreement_index,
        )
        self.assertGreater(
            disagreeing_signals.margin_top1_top2,
            agreeing_signals.margin_top1_top2,
        )


try:
    import fastapi  # noqa: F401

    _HTTP_API_AVAILABLE = True
except ModuleNotFoundError:
    _HTTP_API_AVAILABLE = False


@unittest.skipUnless(_HTTP_API_AVAILABLE, "fastapi not installed in this env")
class DemoProductModeRemovedTests(unittest.TestCase):
    def test_decision_room_executor_demo_is_rejected(self) -> None:
        from decision_room.runtime.http_api import _build_executor

        with self.assertRaisesRegex(
            RuntimeError,
            r"unsupported DECISION_ROOM_EXECUTOR.*'centralized' or 'llm'",
        ):
            _build_executor({"DECISION_ROOM_EXECUTOR": "demo"})


if __name__ == "__main__":
    unittest.main()
