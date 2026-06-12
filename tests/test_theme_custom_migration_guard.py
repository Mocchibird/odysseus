"""Regression: built-in-preset migrations in theme.js must not clobber a USER's
custom theme that shares a retired preset's name.

A custom theme named 'iris' (the name of a removed built-in preset) was being
reset to the default palette on every reload, because getSaved() migrated any
theme with name === 'iris' unconditionally. The colors survived in the custom
list, but the *active* pointer was reset — so the page loaded the default.

Each migration is now guarded against the user's custom-theme map
(`!_customs['<name>']`). This source-pins the guard so it can't regress.
"""
from pathlib import Path

_THEME_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "theme.js"


def test_retired_preset_migrations_skip_user_custom_themes():
    src = _THEME_JS.read_text()
    # The migrations still exist...
    assert "name === 'iris'" in src
    assert "name === 'sakura'" in src
    assert "name === 'chatgpt'" in src
    # ...but each is guarded so it never overwrites a user's custom theme of the
    # same name (the 'iris' one is the destructive reset-to-default).
    assert "!_customs['iris']" in src, (
        "iris migration must skip user custom themes (getSaved + _initWithSync)"
    )
    assert "!_customs['sakura']" in src
    assert "!_customs['chatgpt']" in src
