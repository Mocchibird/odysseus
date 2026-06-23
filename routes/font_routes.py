"""Custom font discovery — lists user-supplied font files in static/fonts/custom/."""
import os
import re
from fastapi import APIRouter

CUSTOM_FONTS_DIR = os.path.join("static", "fonts", "custom")
FONT_EXTENSIONS = {".ttf", ".otf", ".woff", ".woff2"}
FAMILY_SUFFIX_WORDS = ("Display", "Rounded", "Serif", "Sans", "Mono", "Code", "Text")

# Cache the derived family map keyed on the directory's mtime — adding/removing
# a font changes the dir mtime, so the listing + per-file regex parsing only
# re-runs when fonts actually change, not on every font-picker open.
_fonts_cache = None
_fonts_cache_mtime = None


def _split_family_token(token):
    """Split common compact font-family suffixes without breaking brand names."""
    for suffix in FAMILY_SUFFIX_WORDS:
        if token.endswith(suffix) and len(token) > len(suffix):
            return f"{token[:-len(suffix)]} {suffix}"
    return re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', token)


def _derive_family(filename):
    """Derive a font-family name from a filename like 'JetBrainsMono-Regular.woff2' → 'JetBrains Mono'."""
    name = os.path.splitext(filename)[0]
    # Strip common weight/style suffixes
    name = re.sub(
        r'[-_ ]?(Thin|ExtraLight|UltraLight|Light|Regular|Medium|SemiBold|DemiBold|Bold|ExtraBold|UltraBold|Black|Heavy|Italic|Oblique|Variable|VF)$',
        '', name, flags=re.IGNORECASE
    )
    # Replace dashes/underscores with spaces
    name = re.sub(r'[-_]+', ' ', name).strip()
    name = " ".join(_split_family_token(part) for part in name.split())
    return name or filename


def setup_font_routes():
    router = APIRouter(prefix="/api/fonts", tags=["fonts"])

    @router.get("/custom")
    async def list_custom_fonts():
        """Return available custom fonts grouped by derived family name."""
        global _fonts_cache, _fonts_cache_mtime
        os.makedirs(CUSTOM_FONTS_DIR, exist_ok=True)
        try:
            mtime = os.path.getmtime(CUSTOM_FONTS_DIR)
        except OSError:
            mtime = None
        if _fonts_cache is not None and mtime == _fonts_cache_mtime:
            return {"fonts": _fonts_cache}
        families = {}
        for f in sorted(os.listdir(CUSTOM_FONTS_DIR)):
            ext = os.path.splitext(f)[1].lower()
            if ext not in FONT_EXTENSIONS:
                continue
            family = _derive_family(f)
            if family not in families:
                families[family] = []
            families[family].append({
                "file": f,
                "url": f"/static/fonts/custom/{f}",
                "format": ext.lstrip('.'),
            })
        _fonts_cache, _fonts_cache_mtime = families, mtime
        return {"fonts": families}

    return router
