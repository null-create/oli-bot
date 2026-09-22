from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .manager import BuiltinToolManager


def register_tools(manager: "BuiltinToolManager") -> None:
    manager.register_tool(
        name="question",
        description=(
            "Pose questions to the user and wait for their answers before "
            "proceeding. Use this whenever you need the user to make a decision, "
            "choose between options, or provide information only they know. "
            "Pass each question as a separate item; add suggested options when "
            "relevant and mark one as recommended if you believe it is clearly "
            "best. The questions are shown to the user as a widget listing each "
            "question with its options and a 'write your own answer' field; "
            "the tool blocks until the user confirms their answers."
        ),
        parameters={
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": "The question to ask the user.",
                            },
                            "options": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Optional suggested answers, shown as numbered "
                                    "choices the user can select."
                                ),
                            },
                            "recommended": {
                                "type": "string",
                                "description": (
                                    "Optional. The choice you recommend: a 1-based "
                                    "index into 'options' (as text, e.g. '2') or the "
                                    "exact text of one of the options. Omit when no "
                                    "option is clearly best."
                                ),
                            },
                        },
                        "required": ["question"],
                    },
                    "description": "The one or more questions to ask the user.",
                },
            },
            "required": ["questions"],
        },
        handler=lambda questions: _question_handler(questions, manager),
    )


def _resolve_recommended(
    recommended: object, options: list | None
) -> tuple[int | None, str | None]:
    """Resolve a question's ``recommended`` into (option_index, freeform_text).

    1-based option indices (as int or digit string) and exact/partial option
    text matches resolve to an option index; anything else is treated as a
    freeform recommended-action string.
    """
    options = options or []
    if recommended is None:
        return None, None

    if isinstance(recommended, bool):
        return None, None

    if isinstance(recommended, int):
        idx = recommended - 1
        if 0 <= idx < len(options):
            return idx, None
        return None, None

    text = str(recommended).strip()
    if not text:
        return None, None

    if text.isdigit():
        idx = int(text) - 1
        if 0 <= idx < len(options):
            return idx, None

    lowered = text.lower()
    for i, opt in enumerate(options):
        if str(opt).lower() == lowered or lowered in str(opt).lower():
            return i, None

    return None, text


async def _question_handler(
    questions: list[dict], manager: "BuiltinToolManager"
) -> str:
    if not questions:
        return "Error: question called with no questions."

    callback = getattr(manager, "_question_callback", None)
    if callback is None:
        return (
            "Error: the 'question' tool requires an interactive user, but no "
            "question callback is registered in this environment. Proceed using "
            "your best judgment or spell out the question in your reply instead."
        )

    result = callback(questions)
    if inspect.isawaitable(result):
        result = await result
    if result is None:
        return "Error: The user declined to answer the questions."
    return str(result)
