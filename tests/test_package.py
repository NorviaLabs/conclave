"""Tests for the package entry points and the configuration defaults."""

import importlib

from conclave import config


def test_limits_are_positive():
    assert config.MAX_ROUNDS > 0
    assert config.MAX_DELEGATIONS > 0
    assert config.MAX_DELEGATIONS_PER_ROUND > 0
    assert config.MAX_DELEGATIONS_PER_AGENT > 0
    assert config.MAX_DEPTH >= 1
    assert config.MAX_TOOL_STEPS > 0
    assert config.TIMEOUT_SECONDS > 0
    assert config.MAX_TOOL_OUTPUT_CHARS > 0
    assert config.MAX_NOTES > 0
    assert config.MAX_NOTE_CHARS > 0


def test_agent_cap_fits_inside_the_run_cap():
    assert (
        config.MAX_DELEGATIONS_PER_AGENT
        <= config.MAX_DELEGATIONS
    )


def test_package_imports_cleanly():
    package = importlib.import_module("conclave")

    assert package.__doc__


def test_main_module_exposes_run():
    module = importlib.import_module(
        "conclave.__main__"
    )

    assert callable(module.run)
