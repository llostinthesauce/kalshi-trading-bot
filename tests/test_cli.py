import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_cmd_run_does_not_import_beast_mode_bot():
    """cmd_run should import run_bot from main, not BeastModeBot."""
    import inspect
    import cli
    source = inspect.getsource(cli.cmd_run)
    assert "beast_mode_bot" not in source.lower()
    assert "BeastModeBot" not in source


def test_cmd_dashboard_does_not_import_beast_mode_bot():
    import inspect
    import cli
    source = inspect.getsource(cli.cmd_dashboard)
    assert "beast_mode_bot" not in source.lower()
    assert "BeastModeBot" not in source


def test_cli_module_imports_cleanly():
    """cli.py should import without raising ImportError."""
    try:
        import importlib
        import cli
        importlib.reload(cli)
    except ImportError as e:
        assert False, f"cli.py failed to import: {e}"
