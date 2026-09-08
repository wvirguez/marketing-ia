"""The single, centralized source of truth for legal Hypothesis status
transitions (BACKEND-08 §10, mirroring the centralization pattern in
``app/orchestration/transitions.py``). No router or repository ever checks/
applies a transition itself — every Hypothesis status change flows through
``app/strategy/service.py``, which consults this matrix exclusively.

Only ``OPEN -> CONFIRMED`` and ``OPEN -> REFUTED`` are authorized.
``CONFIRMED``/``REFUTED`` have no outgoing edges here — this is a
conservative BACKEND-08 boundary (no reopening a resolved Hypothesis), not
a claim that BACKEND-01 itself defines these two states as permanently
terminal; BACKEND-01's own prose only lists the three status names without
a transition diagram.

No transition matrix exists for Experiment: BACKEND-01 never names an
Experiment status vocabulary at all (see ``app/strategy/models.py``), so
there is nothing to centralize here yet.
"""

from __future__ import annotations

from app.strategy.models import HypothesisStatus

HYPOTHESIS_TRANSITIONS: dict[HypothesisStatus, frozenset[HypothesisStatus]] = {
    HypothesisStatus.OPEN: frozenset({HypothesisStatus.CONFIRMED, HypothesisStatus.REFUTED}),
    HypothesisStatus.CONFIRMED: frozenset(),
    HypothesisStatus.REFUTED: frozenset(),
}


def is_legal_hypothesis_transition(current: HypothesisStatus, target: HypothesisStatus) -> bool:
    return target in HYPOTHESIS_TRANSITIONS[current]
