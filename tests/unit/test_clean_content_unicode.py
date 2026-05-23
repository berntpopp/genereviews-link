"""Regression tests for Unicode-aware whitespace handling in EutilsClient._clean_content."""

from genereview_link.api.eutils_client import EutilsClient

# Unicode whitespace constants (kept as \uXXXX escapes for ASCII-only source per AGENTS.md)
NBSP = "\u00a0"  # U+00A0 NO-BREAK SPACE
THIN_SPACE = "\u2009"  # U+2009 THIN SPACE
NARROW_NBSP = "\u202f"  # U+202F NARROW NO-BREAK SPACE


def test_clean_content_normalizes_unicode_spaces() -> None:
    client = EutilsClient()
    # Mix of NBSP (U+00A0), thin space (U+2009), and narrow no-break space (U+202F)
    # around inline tags -- mirrors the BRCA1-Associated artifact from the reviewer.
    raw = f"<i>BRCA1</i>{NBSP}-{THIN_SPACE}Associated{NARROW_NBSP}HBOC"
    out = client._clean_content(raw)
    assert NBSP not in out
    assert THIN_SPACE not in out
    assert NARROW_NBSP not in out
    assert "  " not in out  # no double spaces


def test_clean_content_is_idempotent() -> None:
    client = EutilsClient()
    raw = f"<i>BRCA1</i>{NBSP}-{THIN_SPACE}Associated{NARROW_NBSP}{NARROW_NBSP}text{NBSP}"
    once = client._clean_content(raw)
    twice = client._clean_content(once)
    assert once == twice
