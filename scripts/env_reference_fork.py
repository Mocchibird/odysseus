"""Fork-only entries for the generated environment reference.

scripts/generate_env_reference.py documents every ODYSSEUS_* variable the
code reads and fails its check when one has no note. The fork's own variables
live here, merged into its VARIABLE_NOTES at import, so the upstream generator
stays a one-line diff. Same shape: name -> (area, audience, summary), where
audience is "user" or "internal" (the generator's USER / INTERNAL).
"""

FORK_VARIABLE_NOTES: dict[str, tuple[str, str, str]] = {
    "ODYSSEUS_PUBLIC_URL": (
        "Calendar, notes and single-user mode", "user",
        "Public base URL of this install (e.g. https://odysseus.example.com). When set, "
        "reminder pushes to ntfy carry Done / Snooze / Tomorrow buttons that call back "
        "here; unset, reminders are sent without action buttons.",
    ),
    "ODYSSEUS_ICS_BLOCK_PRIVATE_IPS": (
        "Calendar, notes and single-user mode", "user",
        "Defaults to on: subscribed ICS feeds may not point at private or loopback "
        "addresses. Set to 0 only to subscribe to a calendar served on your LAN.",
    ),
    "ODYSSEUS_USERCONTENT_ORIGIN": (
        "Upload and media limits", "user",
        "Separate origin (a different host from the app) for serving uploaded HTML as "
        "full standalone pages through signed links. Empty keeps uploaded HTML "
        "sandboxed on the app origin.",
    ),
    "ODYSSEUS_USERCONTENT_SECRET": (
        "Upload and media limits", "user",
        "Signing secret for the standalone user-content links. Defaults to a key file "
        "in the data directory; changing it revokes every link already handed out.",
    ),
    "ODYSSEUS_TRAINING_BURN_CREDIT": (
        "Calendar, notes and single-user mode", "user",
        "Share of logged training calories credited back to the day's intake budget "
        "in Health (0.5 by default, because burn estimates run high).",
    ),
    "ODYSSEUS_REMBG_EXTRA": (
        "Speech and vision models", "user",
        "pip extra used when the background-removal helper (rembg) is installed from "
        "the image editor. Defaults to cpu; set gpu only on an image with CUDA.",
    ),
}
