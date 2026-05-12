"""Tests for ingest audit logging."""

from __future__ import annotations

import logging

from _pytest.logging import LogCaptureFixture

from genereview_link.corpus.nxml import ChapterIngestAudit
from genereview_link.corpus.parallel import _log_audit


def test_log_audit_warns_for_high_cross_reference_ratio(caplog: LogCaptureFixture) -> None:
    audit = ChapterIngestAudit(
        nbk_id="NBK_ROLE",
        parser_version="test-parser",
        role_counts={"evidence": 2, "cross_reference": 1},
    )

    with caplog.at_level(logging.WARNING, logger="genereview_link.corpus.parallel"):
        _log_audit(audit)

    assert [
        record
        for record in caplog.records
        if record.message.startswith("ingest role-distribution nbk=NBK_ROLE")
    ]


def test_log_audit_role_warning_does_not_suppress_content_loss_warning(
    caplog: LogCaptureFixture,
) -> None:
    audit = ChapterIngestAudit(
        nbk_id="NBK_LOSS_ROLE",
        parser_version="test-parser",
        body_text_chars=100,
        captured_text_chars=40,
        role_counts={"evidence": 2, "cross_reference": 1},
    )

    with caplog.at_level(logging.WARNING, logger="genereview_link.corpus.parallel"):
        _log_audit(audit)

    messages = [record.message for record in caplog.records]
    assert any(message.startswith("ingest content-loss nbk=NBK_LOSS_ROLE") for message in messages)
    assert any(
        message.startswith("ingest role-distribution nbk=NBK_LOSS_ROLE") for message in messages
    )
