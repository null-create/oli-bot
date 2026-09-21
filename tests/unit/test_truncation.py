"""Truncation preserves sentence/paragraph boundaries when possible."""

from oli_bot.tools.truncation import TruncationConfig, TruncationManager

_MARKER_PREFIX = "\n\n[... truncated: "


def _body_before_marker(result: str) -> str:
    idx = result.find(_MARKER_PREFIX)
    assert idx != -1, f"Expected truncation marker in result, got: {result!r}"
    return result[:idx]


def test_short_text_is_not_truncated():
    tm = TruncationManager(TruncationConfig(max_chars_small=100, max_chars_large=1000))
    assert tm.truncate("hi there", tier="small") == "hi there"


def test_truncation_prefers_paragraph_boundary():
    para1 = "sentence one. " * 40  # ~560 chars
    para2 = "sentence two. " * 40
    text = para1 + "\n\n" + para2
    tm = TruncationManager(TruncationConfig(max_chars_small=650, max_chars_large=1000))
    result = tm.truncate(text, tier="small")
    body = _body_before_marker(result)
    # Cut must land at the paragraph boundary — para2 must not appear at all
    # and the body must not end mid-word.
    assert "sentence two" not in body
    assert body.endswith("sentence one. " * 40)


def test_truncation_falls_back_to_sentence_boundary():
    text = "This is sentence one. This is sentence two. This is sentence three."
    tm = TruncationManager(TruncationConfig(max_chars_small=50, max_chars_large=1000))
    result = tm.truncate(text, tier="small")
    body = _body_before_marker(result)
    # Sentence boundary trimming: body must end at a period, and must not
    # include a partial third sentence.
    assert body.endswith(".")
    assert "sentence three" not in body
    assert body == "This is sentence one. This is sentence two."


def test_truncation_hard_cut_when_no_boundary_near_limit():
    """When neither a paragraph nor a sentence boundary sits past the 50%
    mark of the limit, the truncator must fall back to a hard character
    cut (no boundary preservation) rather than silently returning the
    full text.
    """
    text = "x" * 200
    tm = TruncationManager(TruncationConfig(max_chars_small=100, max_chars_large=1000))
    result = tm.truncate(text, tier="small")
    body = _body_before_marker(result)
    assert body == "x" * 100
    assert "100 characters remaining" in result


def test_large_tier_uses_larger_limit():
    text = "x" * 5000
    tm = TruncationManager(TruncationConfig(max_chars_small=100, max_chars_large=10000))
    assert tm.truncate(text, tier="large") == text
    assert tm.truncate(text, tier="small") != text
