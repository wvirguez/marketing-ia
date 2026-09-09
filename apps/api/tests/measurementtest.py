"""Shared helpers/fixtures for Measurement tests — real database required.
Metric Entry write paths ARE public (unlike every other bounded context in
this review series), so both service-layer and HTTP-layer test patterns
are used, matching ``tests/test_content_api.py``'s own helper shape for
the parts that remain service-layer-only.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import date
from decimal import Decimal

import pytest

from tests.researchtest import build_campaign_run_with_stages

_request_id_counter = itertools.count()


def next_client_request_id() -> str:
    return f"req-{next(_request_id_counter)}-{uuid.uuid4().hex[:8]}"


def default_metric_values(**overrides: object) -> dict[str, Decimal]:
    payload: dict[str, Decimal] = {"impressions": Decimal("1000"), "clicks": Decimal("50")}
    payload.update(overrides)  # type: ignore[arg-type]
    return payload


def default_period() -> tuple[date, date]:
    return date(2026, 1, 1), date(2026, 1, 31)


@pytest.fixture()
def measurement_campaign(db_session):
    campaign, run, stages = build_campaign_run_with_stages(db_session, campaign_name="Measurement Campaign")
    return campaign, run, stages
