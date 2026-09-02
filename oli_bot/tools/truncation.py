from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TruncationConfig:
    max_chars_small: int = 4000
    max_chars_large: int = 100000


class TruncationManager:
    def __init__(self, config: TruncationConfig | None = None):
        self.config = config or TruncationConfig()

    def truncate(self, text: str, tier: str = "large") -> str:
        limit = (
            self.config.max_chars_small
            if tier == "small"
            else self.config.max_chars_large
        )
        if len(text) <= limit:
            return text

        truncated = text[:limit]

        last_para = truncated.rfind("\n\n")
        if last_para > limit * 0.5:
            truncated = truncated[:last_para]
        else:
            last_sentence = truncated.rfind(". ")
            last_sentence_nl = truncated.rfind(".\n")
            last_sentence = max(last_sentence, last_sentence_nl)
            if last_sentence > limit * 0.5:
                truncated = truncated[: last_sentence + 1]

        remaining = len(text) - len(truncated)
        return f"{truncated}\n\n[... truncated: {remaining} characters remaining]"
