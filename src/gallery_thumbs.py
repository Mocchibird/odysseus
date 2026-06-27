"""Gallery thumbnail / video-poster generation helpers.

Fork-additive module (see docs/fork-additive-policy.md): the gallery grid
serves small cached WebP thumbnails (images) and poster frames (videos) for
`?thumb=1` requests instead of the multi-MB originals. The actual encode/extract
work lives here, in its own dependency-light module, so the upstream
`serve_generated_image` handler in app.py only needs a tiny seam (import + two
calls) rather than carrying these helpers inline — keeping upstream merges
conflict-free.

All third-party/stdlib imports are function-local so this module stays cheap to
import and can never form a circular import with app.py.
"""


def _generate_gallery_thumb(img_path, thumb_dir, thumb_path):
    """Decode/resize/encode a 400px WebP gallery thumbnail.

    CPU-bound (PIL decode + encode), so callers run it via asyncio.to_thread
    to keep it off the event loop. Writes to a temp file and atomically
    renames, so a concurrent first-load never reads or caches a half-written
    thumbnail.
    """
    import os
    import tempfile
    from PIL import Image, ImageOps
    thumb_dir.mkdir(parents=True, exist_ok=True)
    im = Image.open(str(img_path))
    # Bake EXIF rotation into the pixels (PIL drops EXIF on save).
    im = ImageOps.exif_transpose(im)
    im.thumbnail((400, 400))
    if im.mode not in ("RGB", "RGBA", "L"):
        im = im.convert("RGB")
    fd, tmp = tempfile.mkstemp(suffix=".webp", dir=str(thumb_dir))
    os.close(fd)
    try:
        im.save(tmp, "WEBP", quality=80)
        try:
            os.replace(tmp, str(thumb_path))  # atomic publish
        except OSError:
            # Concurrent first-load race: another worker already published this
            # thumb and a response may have it open (Windows blocks replace onto
            # an open file). If the destination now exists, the race is benign.
            if not thumb_path.exists():
                raise
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def _generate_video_poster(video_path, thumb_dir, thumb_path):
    """Extract a poster frame from a video as a 400px-wide WebP via ffmpeg.

    IO/CPU-bound, so callers run it via asyncio.to_thread. Returns True on
    success; False if ffmpeg is unavailable or no frame could be grabbed (the
    caller then falls through to serving the video file). Atomic temp-file
    publish so a concurrent first-load can't read a half-written poster.
    """
    import os
    import shutil
    import subprocess
    import tempfile
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    thumb_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".webp", dir=str(thumb_dir))
    os.close(fd)
    try:
        # Try ~1s in first (skips black leading frames), then frame 0 for clips
        # shorter than a second. Scale to fit 400px wide, keep aspect (even h).
        for ss in ("1", "0"):
            cmd = [
                ffmpeg, "-y", "-ss", ss, "-i", str(video_path),
                "-frames:v", "1", "-vf", "scale='min(400,iw)':-2",
                "-f", "webp", tmp,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if res.returncode == 0 and os.path.getsize(tmp) > 0:
                try:
                    os.replace(tmp, str(thumb_path))  # atomic publish
                except OSError:
                    # Benign concurrent-publish race (see _generate_gallery_thumb).
                    if not thumb_path.exists():
                        raise
                return True
        return False
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


# Generic video placeholder served for ?thumb=1 on a video when no poster frame
# can be produced (no ffmpeg / unreadable clip) — the grid renders video tiles
# as <img>, which can't display video bytes, so this avoids a broken-image icon.
_VIDEO_PLACEHOLDER_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400" viewBox="0 0 400 400">'
    b'<rect width="400" height="400" fill="#1b1b1b"/>'
    b'<circle cx="200" cy="200" r="54" fill="none" stroke="#8a8a8a" stroke-width="6"/>'
    b'<path d="M185 172 L185 228 L233 200 Z" fill="#8a8a8a"/>'
    b'</svg>'
)
