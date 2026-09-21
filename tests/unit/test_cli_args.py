"""Regression tests for chat.py CLI argument parsing."""

import pytest

from oli_bot.chat import _build_arg_parser


def test_load_session_short_flag_matches_long_flag():
    parser = _build_arg_parser()
    short = parser.parse_args(["-s", "abc-123"])
    long = parser.parse_args(["--load-session", "abc-123"])
    assert short.load_session == "abc-123" == long.load_session


def test_no_flags_defaults_to_no_session_selection():
    parser = _build_arg_parser()
    args = parser.parse_args([])
    assert args.load_session is None
    assert args.resume_last is False


def test_resume_last_and_load_session_are_mutually_exclusive():
    parser = _build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--resume-last", "-s", "abc-123"])
