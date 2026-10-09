"""Tests for the package entry points and the configuration defaults."""

import dataclasses
import importlib
import importlib.metadata
import subprocess
import sys

from conclave import (
    RunBudget,
    RunConfig,
)


def test_the_core_ships_no_numbers():
    """The whole point: a library that will not answer for you."""

    for field in dataclasses.fields(RunConfig):
        assert (
            field.default is dataclasses.MISSING
        ), f"{field.name} has a default"

        assert (
            field.default_factory
            is dataclasses.MISSING
        ), f"{field.name} has a factory"

    for field in dataclasses.fields(RunBudget):
        if field.name in {
            "tool_calls",
            "wall_clock_seconds",
            "tokens",
            "cost_usd",
        }:
            # None means unmetered: an absence, not a chosen number.
            assert field.default is None

            continue

        if field.name in {"used", "per_agent", "spent"}:
            continue

        assert (
            field.default is dataclasses.MISSING
        ), f"{field.name} has a default"


def test_a_run_config_carries_what_the_caller_chose():
    config = RunConfig(
        max_tool_steps=3,
        max_notes=2,
        max_note_chars=10,
        max_tool_output_chars=20,
        fan_out_limit=1,
    )

    assert config.max_tool_steps == 3
    assert config.fan_out_limit == 1


def test_package_imports_cleanly():
    package = importlib.import_module("conclave")

    assert package.__doc__

    assert "ToolAgentRuntime" in package.__all__


def run_python(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )


def test_importing_the_core_does_not_pull_the_bundled_tools():
    """Acceptance test: `import conclave` stays a library import."""

    result = run_python(
        "\n".join(
            [
                "import sys",
                "import conclave",
                "assert 'conclave.tools' not in sys.modules",
                "assert 'ddgs' not in sys.modules",
                (
                    "assert not any(name.startswith('conclave.integrations') "
                    "for name in sys.modules)"
                ),
                "print('ok')",
            ]
        )
    )

    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_everything_imports_without_loading_the_scraper():
    """Acceptance test: registering bundled tools does not import `ddgs`."""

    result = run_python(
        "\n".join(
            [
                "import sys, pathlib",
                "sys.modules['ddgs'] = None",
                "import conclave",
                "import conclave.tools",
                (
                    "conclave.tools."
                    "build_tool_registry("
                    "pathlib.Path('.'), "
                    "max_output_chars=20_000)"
                ),
                "print('ok')",
            ]
        )
    )

    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_web_search_dependency_is_included_in_normal_install():
    requirements = importlib.metadata.requires("conclave")
    assert any(requirement.startswith("ddgs") and "extra ==" not in requirement
               for requirement in requirements)


def test_main_module_is_the_command():
    """`python main.py` is kept as an alias for the one command."""

    module = importlib.import_module("main")
    from conclave import cli

    assert module.run is cli.run


def test_console_entry_point_targets_the_app():
    distribution = importlib.metadata.distribution(
        "conclave"
    )

    scripts = {
        entry.name: entry.value
        for entry in distribution.entry_points
        if entry.group == "console_scripts"
    }

    # One command, and the application package it used to sit beside is gone:
    # an installation must not offer a second entry point for it.
    assert scripts == {"conclave": "conclave.cli:run"}


def test_the_library_contains_no_application_domain():
    """Acceptance test: the core stays free of one application's vocabulary."""

    from pathlib import Path

    import conclave

    core = Path(conclave.__file__).parent

    banned = (
        "hypothes",
        "investigation",
        "roadmap",
        # A cast and its currency. A prompt or a refusal that names them is the
        # core assuming which system it is running.
        "coordinator",
        "evidence",
        "researcher",
        "specialist",
    )

    for path in core.rglob("*.py"):
        text = path.read_text().lower()

        for word in banned:
            assert word not in text, (
                f"{path} mentions {word!r}"
            )
