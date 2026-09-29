"""Tests for the package entry points and the configuration defaults."""

import importlib
import importlib.metadata
import subprocess
import sys

from conclave import config


def test_core_limits_are_positive():
    assert config.MAX_DELEGATIONS > 0
    assert config.MAX_DELEGATIONS_PER_AGENT > 0
    assert config.MAX_DEPTH >= 1
    assert config.MAX_TOOL_STEPS > 0
    assert config.TIMEOUT_SECONDS > 0
    assert config.MAX_TOOL_OUTPUT_CHARS > 0
    assert config.MAX_NOTES > 0
    assert config.MAX_NOTE_CHARS > 0
    assert config.FAN_OUT_LIMIT > 0


def test_application_limits_are_positive():
    application = importlib.import_module(
        "investigation.config"
    )

    assert application.MAX_ROUNDS > 0
    assert application.MAX_DELEGATIONS_PER_ROUND > 0


def test_agent_cap_fits_inside_the_run_cap():
    assert (
        config.MAX_DELEGATIONS_PER_AGENT
        <= config.MAX_DELEGATIONS
    )


def test_run_config_defaults_come_from_the_constants():
    default = config.RunConfig()

    assert default is not config.DEFAULT_CONFIG

    assert (
        default.max_tool_steps
        == config.MAX_TOOL_STEPS
    )

    assert (
        default.max_delegation_report_chars
        == config.MAX_TOOL_OUTPUT_CHARS
    )

    assert (
        default.fan_out_limit
        == config.FAN_OUT_LIMIT
    )


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
                "print('ok')",
            ]
        )
    )

    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_everything_imports_without_the_optional_scraper():
    """Acceptance test: `ddgs` is optional, even for the bundled tools."""

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
                    "pathlib.Path('.'))"
                ),
                "print('ok')",
            ]
        )
    )

    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_main_module_exposes_run():
    module = importlib.import_module(
        "investigation.__main__"
    )

    assert callable(module.run)


def test_console_entry_point_targets_the_app():
    distribution = importlib.metadata.distribution(
        "conclave"
    )

    scripts = {
        entry.name: entry.value
        for entry in distribution.entry_points
        if entry.group == "console_scripts"
    }

    assert (
        scripts["conclave"]
        == "investigation.cli:run"
    )


def test_the_library_contains_no_investigation_domain():
    """Acceptance test: the core stays free of this app's vocabulary."""

    from pathlib import Path

    import conclave

    core = Path(conclave.__file__).parent

    banned = (
        "hypothes",
        "investigation",
        "roadmap",
    )

    for path in core.rglob("*.py"):
        text = path.read_text().lower()

        for word in banned:
            assert word not in text, (
                f"{path} mentions {word!r}"
            )
