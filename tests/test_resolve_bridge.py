from src.core.resolve_bridge import load_resolve_api, ResolveBridge

def test_load_resolve_api_env_setup():
    app, msg = load_resolve_api()
    # When DaVinci Resolve is not running, app is None but error message is descriptive
    assert app is None or hasattr(app, "GetProjectManager")
    assert isinstance(msg, str)

def test_resolve_bridge_instance():
    bridge = ResolveBridge()
    assert hasattr(bridge, "connect")
    assert hasattr(bridge, "is_connected")
    assert hasattr(bridge, "ensure_resolve_running")
