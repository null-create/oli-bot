"""Regressions for the shell allowlist and SSRF guard."""

import socket

import pytest

from oli_bot.tools.shell import _is_command_allowed
from oli_bot.tools import web as web_module
from oli_bot.tools.web import _check_ssrf

# ---------- shell allowlist ---------------------------------------------------


def test_git_is_rejected_because_it_is_not_allowlisted():
    err = _is_command_allowed("git push origin main")
    assert err is not None
    assert "git" in err and "allowlist" in err
    err = _is_command_allowed("git status")
    assert err is not None
    assert "git" in err and "allowlist" in err


def test_find_delete_hits_denied_args_table():
    # `-delete` on its own has no metachars, so this reaches the DENIED_ARGS
    # branch and MUST be rejected specifically as an escape-hatch.
    err = _is_command_allowed("find . -delete")
    assert err is not None
    assert "-delete" in err
    assert "escape-hatch" in err


def test_find_exec_hits_denied_args_table():
    # Regression: earlier revisions of this test used `find . -exec rm {} +`,
    # which was actually rejected by the `{` metacharacter check *before*
    # tokenization and never reached DENIED_ARGS at all. Use a form with no
    # forbidden metacharacters so the DENIED_ARGS branch is genuinely
    # exercised.
    err = _is_command_allowed("find . -exec rm foo +")
    assert err is not None
    assert "-exec" in err
    assert "escape-hatch" in err

    err = _is_command_allowed("find . -execdir sh foo +")
    assert err is not None
    assert "-execdir" in err
    assert "escape-hatch" in err


def test_find_fprint_family_blocked():
    err = _is_command_allowed("find . -fprint /tmp/out")
    assert err is not None
    assert "-fprint" in err

    err = _is_command_allowed("find . -ok rm foo +")
    assert err is not None
    assert "-ok" in err


def test_line_continuation_is_rejected_with_explanatory_error():
    # `_segments` splits on '\n', so the trailing backslash on the first
    # segment ("ls \\") fails shlex parsing while the second segment
    # ("rm -rf /") fails the allowlist. Either way, we must not silently
    # succeed and the reported error must be shell-syntax related.
    err = _is_command_allowed("ls \\\nrm -rf /")
    assert err is not None
    assert (
        "Line-continuation" in err
        or "Invalid shell syntax" in err
        or "allowlist" in err
    )
    # Regression: whatever the reason, the smuggled `rm` must not be
    # treated as executable — the returned error must mention *ls* or
    # *rm* only via the rejection reason, never as an accepted command.
    assert "OK" not in err


def test_smuggled_second_command_after_newline_hits_allowlist():
    # If the shlex parse in the first segment ever gets more permissive,
    # the second segment's `rm` must still trip the allowlist.
    err = _is_command_allowed("ls\nrm -rf /")
    assert err is not None
    assert "rm" in err and "allowlist" in err


def test_forbidden_control_char_rejected_explicitly():
    err = _is_command_allowed("ls\rrm")
    assert err is not None
    # Message names the class of chars ("CR, VT, FF, ...").
    assert "control" in err.lower() or "CR" in err


def test_normal_commands_still_allowed():
    assert _is_command_allowed("ls -la") is None
    assert _is_command_allowed("grep foo bar.txt") is None
    assert _is_command_allowed("find . -name '*.py'") is None


# ---------- SSRF --------------------------------------------------------------


def _fake_getaddrinfo(ip: str):
    """Return a getaddrinfo stub that always resolves any host to ``ip``."""

    def _stub(host, *_a, **_k):
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 0, "", (ip, 0))]

    return _stub


def test_ssrf_blocks_loopback(monkeypatch):
    monkeypatch.setattr(
        web_module.socket, "getaddrinfo", _fake_getaddrinfo("127.0.0.1")
    )
    err = _check_ssrf("http://localhost/")
    assert err is not None
    assert "SSRF" in err
    assert "127.0.0.1" in err


def test_ssrf_blocks_link_local_metadata():
    # 169.254.169.254 is a literal IP, no DNS needed.
    err = _check_ssrf("http://169.254.169.254/latest/meta-data/")
    assert err is not None
    assert "SSRF" in err
    assert "169.254.169.254" in err


def test_ssrf_blocks_private_ranges(monkeypatch):
    monkeypatch.setattr(web_module.socket, "getaddrinfo", _fake_getaddrinfo("10.0.0.1"))
    err = _check_ssrf("http://internal.example/")
    assert err is not None
    assert "SSRF" in err

    err = _check_ssrf("http://192.168.1.1/")
    assert err is not None
    assert "SSRF" in err

    err = _check_ssrf("http://[::1]/")
    assert err is not None
    assert "SSRF" in err


def test_ssrf_rejects_non_http_schemes():
    err = _check_ssrf("ftp://example.com/")
    assert err is not None
    assert "http(s)" in err
    assert "ftp" in err

    err = _check_ssrf("file:///etc/passwd")
    assert err is not None
    assert "http(s)" in err


def test_ssrf_allows_public_ipv4(monkeypatch):
    """Hermetic version of the previous public-hosts test — mock DNS so the
    check does not hit the real network."""
    monkeypatch.setattr(
        web_module.socket, "getaddrinfo", _fake_getaddrinfo("140.82.121.4")
    )
    assert _check_ssrf("https://api.github.example/") is None


def test_ssrf_reports_dns_failures():
    def _boom(host, *_a, **_k):
        raise socket.gaierror("nodename nor servname provided")

    import types

    # Patch inline to avoid a module-level fixture requirement.
    real = web_module.socket.getaddrinfo
    web_module.socket.getaddrinfo = _boom  # type: ignore[assignment]
    try:
        err = _check_ssrf("http://definitely-not-a-real-host.invalid/")
        assert err is not None
        assert "DNS" in err
    finally:
        web_module.socket.getaddrinfo = real  # type: ignore[assignment]


def test_ssrf_rejects_url_with_no_host():
    err = _check_ssrf("http:///path")
    assert err is not None
    assert "no host" in err


# --------------------------------------------------------------------------- #
# Expanded shell allowlist (sed/awk/xargs/jq/redirects)                        #
# --------------------------------------------------------------------------- #


def test_shell_allows_sed_read():
    from oli_bot.tools.shell import _is_command_allowed

    assert _is_command_allowed("sed -n '1,20p' agent.py") is None


def test_shell_blocks_sed_in_place():
    from oli_bot.tools.shell import _is_command_allowed

    err = _is_command_allowed("sed -i 's/a/b/' agent.py")
    assert err is not None and "escape-hatch" in err


def test_shell_blocks_sed_in_place_with_suffix():
    from oli_bot.tools.shell import _is_command_allowed

    err = _is_command_allowed("sed -i.bak 's/a/b/' agent.py")
    assert err is not None and "escape-hatch" in err


def test_shell_allows_awk_inline_script():
    from oli_bot.tools.shell import _is_command_allowed

    assert _is_command_allowed("awk '{print $1}' agent.py") is None


def test_shell_blocks_awk_script_file():
    from oli_bot.tools.shell import _is_command_allowed

    err = _is_command_allowed("awk -f script.awk agent.py")
    assert err is not None and "escape-hatch" in err


def test_shell_allows_find_pipe_xargs_grep():
    from oli_bot.tools.shell import _is_command_allowed

    assert _is_command_allowed("find . -name '*.py' | xargs grep -l TODO") is None


def test_shell_blocks_xargs_with_non_allowlisted_inner_command():
    from oli_bot.tools.shell import _is_command_allowed

    err = _is_command_allowed("find . -name '*.py' | xargs rm")
    assert err is not None and "xargs cannot invoke 'rm'" in err


def test_shell_allows_xargs_with_allowlisted_inner_after_flags():
    from oli_bot.tools.shell import _is_command_allowed

    # -n and -I take value args; the inner command starts after them. The
    # ``{}`` placeholder must be quoted so it does not trip the general
    # brace-expansion blocker.
    assert _is_command_allowed("echo a | xargs -n 1 -I '{}' cat '{}'") is None


def test_shell_allows_jq_pipeline():
    from oli_bot.tools.shell import _is_command_allowed

    assert _is_command_allowed("cat data.json | jq '.items[].name'") is None


def test_shell_blocks_redirect_without_workspace():
    from oli_bot.tools.shell import _is_command_allowed

    err = _is_command_allowed("ls > out.txt", workspace=None)
    assert err is not None and "workspace" in err


def test_shell_allows_redirect_inside_workspace(tmp_path):
    from oli_bot.tools.shell import _is_command_allowed

    # Redirect target is a bare filename that resolves under cwd; the check
    # uses Path.resolve() so we invoke it with a path we know is inside.
    target = tmp_path / "out.txt"
    cmd = f"ls > {target}"
    assert _is_command_allowed(cmd, workspace=tmp_path) is None


def test_shell_blocks_redirect_outside_workspace(tmp_path):
    from oli_bot.tools.shell import _is_command_allowed

    outside = tmp_path.parent / "escape.txt"
    err = _is_command_allowed(f"ls > {outside}", workspace=tmp_path)
    assert err is not None and "outside" in err


def test_shell_allows_append_redirect_inside_workspace(tmp_path):
    from oli_bot.tools.shell import _is_command_allowed

    target = tmp_path / "log.txt"
    assert _is_command_allowed(f"echo hi >> {target}", workspace=tmp_path) is None


def test_shell_still_blocks_input_redirect():
    from oli_bot.tools.shell import _is_command_allowed

    err = _is_command_allowed("cat < /etc/passwd")
    assert err is not None and "Input redirects" in err


def test_shell_still_blocks_find_exec():
    from oli_bot.tools.shell import _is_command_allowed

    # -exec is on the DENIED_ARGS list; a plain form without placeholder or
    # quoted terminator must still be rejected.
    err = _is_command_allowed("find . -name x -exec ls")
    assert err is not None and "escape-hatch" in err


def test_shell_allows_tee_and_pipe():
    from oli_bot.tools.shell import _is_command_allowed

    # tee itself is in the allowlist; whether the file target is workspace-
    # scoped is a runtime concern for the tee syscall, not the allowlister.
    assert _is_command_allowed("echo hi | tee log.txt") is None


# --------------------------------------------------------------------------- #
# Language runtimes                                                            #
# --------------------------------------------------------------------------- #


def test_shell_allows_python_pytest_and_similar_runtimes():
    assert _is_command_allowed("python -m pytest") is None
    assert _is_command_allowed("python3 script.py") is None
    assert _is_command_allowed("pytest tests/ -q") is None
    assert _is_command_allowed("node script.js") is None
    assert _is_command_allowed("npm test") is None
    assert _is_command_allowed("npx tsc --noEmit") is None
    assert _is_command_allowed("pip list") is None
    assert _is_command_allowed("uv pip list") is None


def test_shell_allows_python_with_absolute_venv_path():
    # Model often invokes `.../venv/bin/python`; basename check must accept it.
    assert _is_command_allowed("/Users/x/venv/bin/python -m pytest tests/") is None


def test_shell_allows_python_dash_c_by_design():
    # Once an interpreter is allowlisted the sandbox for it is advisory —
    # `-c` is explicitly allowed (documented trade-off).
    assert _is_command_allowed('python -c "print(1)"') is None


def test_shell_still_rejects_smuggled_rm_after_python():
    err = _is_command_allowed("python -m pytest ; rm -rf /tmp/x")
    assert err is not None
    assert "rm" in err and "allowlist" in err


def test_shell_still_rejects_xargs_rm_even_with_python_allowed():
    err = _is_command_allowed("echo foo | xargs rm")
    assert err is not None and "xargs cannot invoke 'rm'" in err


# --------------------------------------------------------------------------- #
# Stderr redirects and device targets                                          #
# --------------------------------------------------------------------------- #


def test_shell_allows_stderr_fd_duplication_without_workspace():
    # `2>&1` never touches the filesystem, so no workspace is required.
    assert _is_command_allowed("pytest -q 2>&1 | tail -5", workspace=None) is None
    assert _is_command_allowed("python -m pytest 2>&1", workspace=None) is None


def test_shell_allows_stderr_close():
    assert _is_command_allowed("pytest -q 2>&-", workspace=None) is None


def test_shell_allows_device_null_and_std_targets():
    assert _is_command_allowed("ls 2>/dev/null", workspace=None) is None
    assert _is_command_allowed("echo hi > /dev/null", workspace=None) is None
    assert _is_command_allowed("echo hi > /dev/stderr", workspace=None) is None
    assert _is_command_allowed("echo hi >> /dev/stdout", workspace=None) is None


def test_shell_fd_dup_carveout_does_not_leak_to_real_files(tmp_path):
    # Regression: fd-dup regex must not swallow ordinary redirect targets.
    outside = tmp_path.parent / "escape.txt"
    err = _is_command_allowed(f"ls > {outside}", workspace=tmp_path)
    assert err is not None and "outside" in err

    err = _is_command_allowed("ls > /etc/hosts", workspace=tmp_path)
    assert err is not None and "outside" in err


def test_shell_still_rejects_real_file_redirect_without_workspace():
    err = _is_command_allowed("ls > out.txt", workspace=None)
    assert err is not None and "workspace" in err
