#!/usr/bin/env python3
"""css_media_audit.py — find selectors defined BOTH at desktop scope AND inside a
`@media` block (the "I edited the desktop rule but the mobile @media override
silently won/masked it" footgun the ROADMAP flags).

Usage:
    python scripts/css_media_audit.py                  # audit static/style.css
    python scripts/css_media_audit.py path/to.css      # audit another file
    python scripts/css_media_audit.py --selector .foo  # show every definition of one selector

It's a heuristic line scanner (skips comments + strings, tracks brace depth and
the enclosing @media condition) — good enough to surface paired rules, not a CSS
compiler. Pure stdlib; safe (read-only).
"""
from __future__ import annotations

import sys
from collections import defaultdict


def _scan(css: str):
    """Return {selector: [(line, media_condition_or_None), ...]}."""
    out: dict[str, list] = defaultdict(list)
    stack: list[str] = []          # one entry per open `{` — the @media cond, '@AT', or '@RULE'
    buf: list[str] = []            # text since the last `{`/`}`/`;`
    line = 1
    i, n = 0, len(css)
    quote = None                   # current string delimiter, or None
    while i < n:
        c = css[i]
        if c == "\n":
            line += 1
        # skip strings
        if quote:
            if c == "\\":
                buf.append(css[i:i + 2]); i += 2; continue
            if c == quote:
                quote = None
            buf.append(c); i += 1; continue
        if c in "\"'":
            quote = c; buf.append(c); i += 1; continue
        # skip comments
        if css[i:i + 2] == "/*":
            j = css.find("*/", i + 2)
            j = n if j == -1 else j + 2
            line += css.count("\n", i, j)
            i = j; continue
        if c == "{":
            head = "".join(buf).strip()
            buf = []
            if head.startswith("@"):
                stack.append(head)         # keep full text (@media cond / @keyframes name / ...)
            elif any("keyframes" in s for s in stack):
                stack.append("@RULE")      # a keyframe step (0% / from / to) — not a selector
            else:
                media = next((s for s in reversed(stack) if s.startswith("@media")), None)
                for sel in (s.strip() for s in head.split(",")):
                    if sel:
                        out[sel].append((line, media))
                stack.append("@RULE")      # declaration block — balance only
            i += 1; continue
        if c == "}":
            if stack:
                stack.pop()
            buf = []
            i += 1; continue
        if c == ";":
            buf = []                       # end of a declaration / at-statement
            i += 1; continue
        buf.append(c); i += 1
    return out


def main(argv):
    path = "static/style.css"
    want = None
    args = argv[1:]
    if "--selector" in args:
        k = args.index("--selector")
        want = args[k + 1] if k + 1 < len(args) else None
        args = args[:k] + args[k + 2:]
    if args:
        path = args[0]

    with open(path, encoding="utf-8") as fh:
        defs = _scan(fh.read())

    if want:
        hits = defs.get(want, [])
        if not hits:
            print(f"No definitions found for selector: {want}")
            return 0
        print(f"{want} - {len(hits)} definition(s):")
        for ln, media in hits:
            print(f"  line {ln:>6}  {media or 'desktop (no @media)'}")
        return 0

    # Selectors defined at desktop scope AND in >=1 @media block.
    paired = []
    for sel, hits in defs.items():
        media_hits = [(ln, m) for ln, m in hits if m]
        desktop_hits = [ln for ln, m in hits if not m]
        if desktop_hits and media_hits:
            paired.append((sel, desktop_hits, media_hits))
    paired.sort(key=lambda r: len(r[1]) + len(r[2]), reverse=True)

    print(f"{path}: {len(paired)} selectors defined at BOTH desktop and @media scope")
    print("(edit the desktop rule and a @media override may mask it - check both)\n")
    for sel, desktop_hits, media_hits in paired:
        d = ", ".join(f"L{ln}" for ln in desktop_hits)
        m = ", ".join(f"L{ln} {cond[:48]}" for ln, cond in media_hits)
        print(f"{sel}\n    desktop: {d}\n    media:   {m}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
