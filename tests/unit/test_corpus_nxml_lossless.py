"""Guardrail tests for the no-loss chunker.

See docs/superpowers/specs/2026-05-12-chunker-data-loss-findings.md.

These tests prove that <list>, <def-list>, <boxed-text>, and
<table-wrap-foot> content reaches PassageRecords (was being silently
dropped before 2026-05-12) and that the per-chapter audit ledger
balances and flags unknown tags.
"""

from __future__ import annotations

from textwrap import dedent

import pytest

from genereview_link.corpus.nxml import (
    CAPTURE_TAGS,
    KNOWN_SKIP_TAGS,
    PARSER_VERSION,
    STRUCTURAL_TAGS,
    ChapterIngestAudit,
    parse_and_chunk_one,
)
from genereview_link.corpus.nxml_render import (
    render_boxed_text,
    render_def_list,
    render_list,
)


def _make_nxml(body_xml: str, *, title: str = "Test Chapter") -> bytes:
    """Wrap a body fragment as a minimal valid book-part NXML."""
    raw = dedent(
        f"""\
        <book-part>
          <book-part-meta>
            <title-group><title>{title}</title></title-group>
            <pub-history>
              <date date-type="created">
                <year>2020</year><month>1</month><day>1</day>
              </date>
              <date date-type="updated">
                <year>2024</year><month>1</month><day>1</day>
              </date>
            </pub-history>
          </book-part-meta>
          <body>{body_xml}</body>
        </book-part>
        """
    )
    return raw.encode("utf-8")


# ---------------------------------------------------------------------------
# Capture proof: text inside <list> / <def-list> / <boxed-text> must
# reach a passage.
# ---------------------------------------------------------------------------


def test_list_content_reaches_passage() -> None:
    """Bug pre-2026-05-12: <list> children of <sec> were silently dropped.

    The reviewer hit this on NBK1247 Prevention of Primary Manifestations
    where prophylactic surgery bullets were missing.
    """
    raw = _make_nxml(
        """
        <sec><title>Prevention</title>
          <p>Breast cancer</p>
          <list list-type="bullet">
            <list-item><p>Consider GUARDRAIL_CANARY_MASTECTOMY.</p></list-item>
            <list-item><p>Consider GUARDRAIL_CANARY_OOPHORECTOMY.</p></list-item>
          </list>
        </sec>
        """
    )
    _, passages, audit = parse_and_chunk_one(
        raw, nbk_id="NBK_T1", short_name="t1", nxml_relpath="t1.nxml"
    )
    blob = " ".join(p.text for p in passages)
    assert "GUARDRAIL_CANARY_MASTECTOMY" in blob
    assert "GUARDRAIL_CANARY_OOPHORECTOMY" in blob
    assert audit.list_renders == 1


def test_def_list_content_reaches_passage() -> None:
    """<def-list> with term/def pairs must be captured."""
    raw = _make_nxml(
        """
        <sec><title>Glossary</title>
          <def-list>
            <def-item>
              <term>HBOC</term>
              <def><p>GUARDRAIL_CANARY_HBOC_DEFINITION.</p></def>
            </def-item>
          </def-list>
        </sec>
        """
    )
    _, passages, audit = parse_and_chunk_one(
        raw, nbk_id="NBK_T2", short_name="t2", nxml_relpath="t2.nxml"
    )
    blob = " ".join(p.text for p in passages)
    assert "GUARDRAIL_CANARY_HBOC_DEFINITION" in blob
    assert "HBOC" in blob
    assert audit.def_list_renders == 1


def test_boxed_text_content_reaches_passage() -> None:
    """<boxed-text> clinical notes must be captured."""
    raw = _make_nxml(
        """
        <sec><title>Note</title>
          <boxed-text>
            <caption><title>Clinical Pearl</title></caption>
            <p>GUARDRAIL_CANARY_BOXED_CONTENT.</p>
          </boxed-text>
        </sec>
        """
    )
    _, passages, audit = parse_and_chunk_one(
        raw, nbk_id="NBK_T3", short_name="t3", nxml_relpath="t3.nxml"
    )
    blob = " ".join(p.text for p in passages)
    assert "GUARDRAIL_CANARY_BOXED_CONTENT" in blob
    assert "Clinical Pearl" in blob
    assert audit.boxed_text_renders == 1


def test_nested_list_inside_list_item() -> None:
    """Nested <list> inside <list-item> must be captured recursively."""
    raw = _make_nxml(
        """
        <sec><title>Stepwise</title>
          <list list-type="order">
            <list-item><label>1.</label>
              <p>Outer step.</p>
              <list list-type="bullet">
                <list-item><p>GUARDRAIL_CANARY_NESTED_BULLET.</p></list-item>
              </list>
            </list-item>
          </list>
        </sec>
        """
    )
    _, passages, _audit = parse_and_chunk_one(
        raw, nbk_id="NBK_T4", short_name="t4", nxml_relpath="t4.nxml"
    )
    blob = " ".join(p.text for p in passages)
    assert "GUARDRAIL_CANARY_NESTED_BULLET" in blob
    assert "Outer step" in blob


def test_table_footnote_content_reaches_passage() -> None:
    """<table-wrap-foot> content must appear in the rendered table passage."""
    raw = _make_nxml(
        """
        <sec><title>Surveillance</title>
          <table-wrap id="t5">
            <caption><title>Test Surveillance</title></caption>
            <table>
              <thead><tr><th>System</th><th>Evaluation</th></tr></thead>
              <tbody><tr><td>Breast</td><td>MRI</td></tr></tbody>
            </table>
            <table-wrap-foot>
              <p>GUARDRAIL_CANARY_FOOTNOTE: based on NCCN guidelines.</p>
            </table-wrap-foot>
          </table-wrap>
        </sec>
        """
    )
    _, passages, _audit = parse_and_chunk_one(
        raw, nbk_id="NBK_T5", short_name="t5", nxml_relpath="t5.nxml"
    )
    table_passages = [p for p in passages if p.passage_type == "table"]
    assert len(table_passages) == 1
    assert "GUARDRAIL_CANARY_FOOTNOTE" in table_passages[0].text


# ---------------------------------------------------------------------------
# Audit ledger conservation: captured + structural + skipped + unknown
# >= body (modulo chunk overlap).
# ---------------------------------------------------------------------------


def test_audit_balances_for_simple_chapter() -> None:
    raw = _make_nxml(
        """
        <sec><title>S1</title>
          <p>Alpha beta gamma.</p>
          <list>
            <list-item><p>Delta epsilon zeta.</p></list-item>
          </list>
        </sec>
        <sec><title>S2</title>
          <p>Eta theta iota.</p>
        </sec>
        """
    )
    _, _, audit = parse_and_chunk_one(raw, nbk_id="NBK_A1", short_name="a1", nxml_relpath="a1.nxml")
    assert audit.parser_version == PARSER_VERSION
    assert audit.unaccounted_ratio == 0.0
    assert audit.unknown_tags_with_text == {}
    assert audit.passage_count >= 1


def test_audit_records_known_skip_tags() -> None:
    raw = _make_nxml(
        """
        <sec><title>S1</title>
          <p>Some prose.</p>
          <ref-list>
            <ref>GUARDRAIL_SKIPPED_REFERENCE_TEXT.</ref>
          </ref-list>
        </sec>
        """
    )
    _, _, audit = parse_and_chunk_one(raw, nbk_id="NBK_A2", short_name="a2", nxml_relpath="a2.nxml")
    assert audit.skipped_by_tag.get("ref-list", 0) > 0


def test_audit_flags_unknown_tag_with_text() -> None:
    """If NCBI introduces a new tag we haven't classified, it must be
    surfaced in unknown_tags_with_text — never silently dropped.
    """
    raw = _make_nxml(
        """
        <sec><title>Future</title>
          <p>Normal paragraph.</p>
          <future-clinical-decision-aid>
            <p>GUARDRAIL_CANARY_UNKNOWN_TAG_TEXT</p>
          </future-clinical-decision-aid>
        </sec>
        """
    )
    _, _, audit = parse_and_chunk_one(raw, nbk_id="NBK_A3", short_name="a3", nxml_relpath="a3.nxml")
    assert "future-clinical-decision-aid" in audit.unknown_tags_with_text
    assert audit.unknown_tags_with_text["future-clinical-decision-aid"] > 0


# ---------------------------------------------------------------------------
# Tag-policy invariants
# ---------------------------------------------------------------------------


def test_tag_policy_sets_are_disjoint() -> None:
    """CAPTURE/STRUCTURAL/KNOWN_SKIP must not overlap — a tag has exactly
    one policy.
    """
    cap = set(CAPTURE_TAGS)
    struct = set(STRUCTURAL_TAGS)
    skip = set(KNOWN_SKIP_TAGS.keys())
    assert cap & struct == set()
    assert cap & skip == set()
    assert struct & skip == set()


def test_known_skip_tags_each_have_a_reason() -> None:
    for tag, reason in KNOWN_SKIP_TAGS.items():
        assert reason.strip(), f"KNOWN_SKIP_TAGS[{tag}] missing reason"


# ---------------------------------------------------------------------------
# Renderer-level unit tests (no NXML wrapping)
# ---------------------------------------------------------------------------


def test_render_list_handles_order_labels() -> None:
    from lxml import etree as _etree

    xml = """
    <list list-type="order">
      <list-item><label>1.</label><p>First.</p></list-item>
      <list-item><label>2.</label><p>Second.</p></list-item>
    </list>
    """
    el = _etree.fromstring(xml)
    out = render_list(el)
    assert "1. First." in out
    assert "2. Second." in out


def test_render_def_list_produces_term_def_pairs() -> None:
    from lxml import etree as _etree

    xml = """
    <def-list>
      <def-item><term>HBOC</term><def><p>Hereditary breast/ovarian cancer.</p></def></def-item>
    </def-list>
    """
    el = _etree.fromstring(xml)
    out = render_def_list(el)
    assert "HBOC" in out
    assert "Hereditary breast/ovarian cancer." in out


def test_render_boxed_text_keeps_caption_and_body() -> None:
    from lxml import etree as _etree

    xml = """
    <boxed-text>
      <caption><title>Alert</title></caption>
      <p>Important note.</p>
    </boxed-text>
    """
    el = _etree.fromstring(xml)
    out = render_boxed_text(el)
    assert "Alert" in out
    assert "Important note." in out


# ---------------------------------------------------------------------------
# Backward-compat: existing 3-element return shape
# ---------------------------------------------------------------------------


def test_parse_and_chunk_one_returns_three_tuple() -> None:
    raw = _make_nxml("<sec><title>S</title><p>Hi.</p></sec>")
    result = parse_and_chunk_one(raw, nbk_id="NBK_R", short_name="r", nxml_relpath="r.nxml")
    assert len(result) == 3
    chapter, passages, audit = result
    assert chapter.nbk_id == "NBK_R"
    assert isinstance(passages, list)
    assert isinstance(audit, ChapterIngestAudit)


# ---------------------------------------------------------------------------
# Flat-body chapter (no <sec> children, just <p>/<list>) — e.g. updates.nxml
# ---------------------------------------------------------------------------


def test_flat_body_with_no_sec_still_captures_content() -> None:
    raw = _make_nxml(
        """
        <p>GUARDRAIL_CANARY_FLAT_BODY_PARA.</p>
        <list><list-item><p>GUARDRAIL_CANARY_FLAT_BODY_LIST.</p></list-item></list>
        """,
        title="Updates",
    )
    _, passages, audit = parse_and_chunk_one(
        raw, nbk_id="NBK_FLAT", short_name="flat", nxml_relpath="flat.nxml"
    )
    blob = " ".join(p.text for p in passages)
    assert "GUARDRAIL_CANARY_FLAT_BODY_PARA" in blob
    assert "GUARDRAIL_CANARY_FLAT_BODY_LIST" in blob
    assert audit.unaccounted_ratio == 0.0


# ---------------------------------------------------------------------------
# Smoke: existing typical.nxml fixture still passes (regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_typical_fixture_still_parses_with_audit() -> None:
    from pathlib import Path

    fixtures = Path(__file__).parent.parent / "fixtures" / "nxml"
    raw = (fixtures / "typical.nxml").read_bytes()
    chapter, passages, audit = parse_and_chunk_one(
        raw, nbk_id="NBK1247", short_name="brca1", nxml_relpath="brca1.nxml"
    )
    assert chapter.nbk_id == "NBK1247"
    assert len(passages) > 0
    assert audit.parser_version == PARSER_VERSION
