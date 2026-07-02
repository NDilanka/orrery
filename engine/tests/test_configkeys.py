"""Coverage for ``loop.configkeys`` — the shared camel/snake key resolver + unknown-key warner
used by every ``*_from`` / ``from_loop_json`` config parser (EngineConfig, BmadConfig, QaConfig)."""

from __future__ import annotations

from loop.configkeys import resolve, warn_unknown_keys


def test_resolve_prefers_first_present_key():
    assert resolve({"a": 1, "b": 2}, "a", "b") == 1
    assert resolve({"b": 2}, "a", "b") == 2


def test_resolve_skips_none_values():
    assert resolve({"a": None, "b": 2}, "a", "b") == 2


def test_resolve_returns_default_when_absent():
    assert resolve({}, "a", "b", default="x") == "x"
    assert resolve({}, "a", "b") is None


def test_warn_unknown_keys_warns_on_stderr_for_typo(capsys):
    warn_unknown_keys({"maxTurns": 5, "maxTurnss": 5}, {"maxTurns"}, "engine")
    err = capsys.readouterr().err
    assert "maxTurnss" in err
    assert "unrecognized" in err
    assert "maxTurns" not in err.replace("maxTurnss", "")  # the KNOWN key never warns


def test_warn_unknown_keys_silent_when_all_known(capsys):
    warn_unknown_keys({"a": 1, "b": 2}, {"a", "b"}, "engine")
    assert capsys.readouterr().err == ""


def test_warn_unknown_keys_retired_gets_gentler_message(capsys):
    warn_unknown_keys({"greenWhen": "exit==0"}, set(), "engine.gate", retired={"greenWhen"})
    err = capsys.readouterr().err
    assert "retired" in err
    assert "greenWhen" in err


def test_warn_unknown_keys_noop_on_non_dict():
    # Must not raise when the resolved block isn't a dict (defensive — callers already guard).
    warn_unknown_keys(None, {"a"}, "engine")  # type: ignore[arg-type]
