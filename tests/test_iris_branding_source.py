from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_iris_is_default_theme_and_icon_brand():
    theme = _read("static/js/theme.js")
    manifest = _read("static/manifest.json")

    assert "iris:" in theme
    assert "const DEFAULT_THEME = 'iris';" in theme
    assert "iris-theme-default-v1" in theme
    assert "static/IRIS.png" in manifest
    assert "iris-icon.svg" not in manifest
    assert '"name": "Iris"' in manifest


def test_main_and_login_surfaces_use_iris_mark_not_boat():
    combined = "\n".join([
        _read("static/index.html"),
        _read("static/login.html"),
        _read("static/js/theme.js"),
    ])

    assert "welcome-mark" in combined
    assert "logo-mark" in combined
    assert "Iris Chat" in combined
    assert "Message Iris..." in combined
    assert "M16 4L16 22" not in combined
    assert "welcome-boat" not in combined
    assert "logo-boat" not in combined
