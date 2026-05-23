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


# Compatibility-character constants for the NFC/NFKC fidelity tests
# (kept as \uXXXX escapes for ASCII-only source per AGENTS.md).
SUPERSCRIPT_2 = "\u00b2"  # SUPERSCRIPT TWO -- NFKC folds to '2'
MICRO_SIGN = "\u00b5"  # MICRO SIGN -- NFKC folds to U+03BC GREEK SMALL MU
GREEK_MU = "\u03bc"  # GREEK SMALL LETTER MU
LATIN_LIGATURE_FI = "\ufb01"  # LATIN SMALL LIGATURE FI -- NFKC expands to "fi"
FULLWIDTH_LT = "\uff1c"  # FULLWIDTH LESS-THAN SIGN -- NFKC folds to '<'
FULLWIDTH_GT = "\uff1e"  # FULLWIDTH GREATER-THAN SIGN -- NFKC folds to '>'


def test_clean_content_preserves_clinical_and_scientific_notation() -> None:
    """NFC (not NFKC) is used so compatibility characters are preserved verbatim.

    GeneReviews chapters use units (m^2, ug/dL), the fi ligature in proper
    typography, and superscripts in dosing notation. NFKC would silently fold
    these to ASCII (m^2 -> m2, ug -> mug with Greek mu, fi -> 'fi'), corrupting
    citation- and variant-grade text. NFC leaves them alone.
    """
    client = EutilsClient()
    # Superscript-2 must survive
    assert SUPERSCRIPT_2 in client._clean_content(f"dose 150 mg/m{SUPERSCRIPT_2}")
    # Micro sign U+00B5 must NOT be folded to Greek mu U+03BC
    out_micro = client._clean_content(f"dose 50 {MICRO_SIGN}g/dL")
    assert MICRO_SIGN in out_micro
    assert GREEK_MU not in out_micro
    # fi ligature must survive (NFKC would expand to 'fi')
    out_lig = client._clean_content(f"{LATIN_LIGATURE_FI}nal")
    assert LATIN_LIGATURE_FI in out_lig


def test_clean_content_does_not_reintroduce_tag_chars_from_fullwidth() -> None:
    """Defense-in-depth: full-width angle brackets must not become ASCII '<>'.

    NFKC would fold full-width forms (U+FF1C, U+FF1E) to ASCII '<', '>' AFTER
    the HTML-strip regex has already run, reintroducing tag-like sequences in
    the cleaned output. NFC does not fold these, so the full-width characters
    pass through verbatim (which is what we want -- they are not HTML).
    """
    client = EutilsClient()
    raw = f"{FULLWIDTH_LT}script{FULLWIDTH_GT}"
    out = client._clean_content(raw)
    # Full-width characters preserved; no ASCII '<'/'>' reintroduced.
    assert "<" not in out
    assert ">" not in out
