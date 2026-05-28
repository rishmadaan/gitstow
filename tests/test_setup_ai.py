"""Tests for AI integration setup prompts."""

from io import StringIO

from rich.console import Console

from gitstow.cli import setup_ai as setup_ai_module


def test_setup_ai_mcp_warning_markup_renders(monkeypatch):
    """Standalone setup-ai should render the MCP warning without markup errors."""
    output = StringIO()

    monkeypatch.setattr(
        setup_ai_module,
        "console",
        Console(file=output, force_terminal=False),
    )
    monkeypatch.setattr(
        setup_ai_module,
        "_detect_ai_tools",
        lambda: [{"name": "Cursor", "type": "mcp_config", "path": "mcp.json"}],
    )
    monkeypatch.setattr(setup_ai_module, "_setup_mcp_config", lambda *_args, **_kwargs: None)

    setup_ai_module.setup_ai(auto=False, quiet=False)

    assert "MCP Server" in output.getvalue()


def test_onboard_ai_integration_mcp_warning_markup_renders(monkeypatch):
    """Onboarding's AI section should render the MCP warning without markup errors."""
    output = StringIO()

    monkeypatch.setattr(
        setup_ai_module,
        "console",
        Console(file=output, force_terminal=False),
    )
    monkeypatch.setattr(
        setup_ai_module,
        "_detect_ai_tools",
        lambda: [{"name": "Cursor", "type": "mcp_config", "path": "mcp.json"}],
    )
    monkeypatch.setattr(setup_ai_module, "_setup_mcp_config", lambda *_args, **_kwargs: None)

    setup_ai_module._setup_ai_integrations()

    assert "MCP server" in output.getvalue()
