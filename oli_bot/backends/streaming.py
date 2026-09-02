from __future__ import annotations


class _StreamingThinkParser:
    """Streaming state-machine that splits <think>…</think> tags from regular content.

    Handles tags split across chunk boundaries by buffering potential partial-tag
    sequences.  Call ``feed(text)`` for each incoming chunk; call ``flush()`` at
    end-of-stream to drain any buffered remainder.  Each method returns a list of
    ``(kind, text)`` tuples where *kind* is ``"thinking"`` or ``"text"``.
    """

    _OPEN = "<think>"
    _CLOSE = "</think>"

    def __init__(self) -> None:
        self._in_thinking = False
        self._buf = ""

    def feed(self, text: str) -> list[tuple[str, str]]:
        self._buf += text
        results: list[tuple[str, str]] = []
        tag = self._CLOSE if self._in_thinking else self._OPEN
        while True:
            idx = self._buf.find(tag)
            if idx == -1:
                hold = 0
                for length in range(1, len(tag)):
                    if self._buf.endswith(tag[:length]):
                        hold = length
                        break
                flush_end = len(self._buf) - hold
                if flush_end > 0:
                    results.append(
                        (
                            "thinking" if self._in_thinking else "text",
                            self._buf[:flush_end],
                        )
                    )
                self._buf = self._buf[flush_end:]
                break
            if idx > 0:
                results.append(
                    ("thinking" if self._in_thinking else "text", self._buf[:idx])
                )
            self._buf = self._buf[idx + len(tag) :]
            self._in_thinking = not self._in_thinking
            tag = self._CLOSE if self._in_thinking else self._OPEN
        return results

    def flush(self) -> list[tuple[str, str]]:
        if not self._buf:
            return []
        result = [("thinking" if self._in_thinking else "text", self._buf)]
        self._buf = ""
        return result


__all__ = ["_StreamingThinkParser"]
