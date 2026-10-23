"""Fork-only capability families for the turn contract and request authority.

Upstream admits a tool only when the request's semantic family grants it
(``src.turn_contract.FAMILY_TOOLS``, resolved by ``requested_capabilities`` and
enforced by ``src.agent_runtime.authority.create_request_authority``). Tools in
no family are offered to the model and then refused at dispatch, so the fork's
own tools have to be registered here or Iris can never run them.

Registration follows upstream's posture: a fork tool is granted when the
request is about its domain, not on every turn. Being in ALWAYS_AVAILABLE means
a tool is *offered*; it is *authorized* only through its family.

The fork's trigger words stay private to this module. Adding them to upstream's
``_FAMILY_WORDS`` / ``_FUZZY_FAMILY_TERMS`` would feed upstream's own routing
(typo correction, single-family resolution, its app_api gallery path) and change
how non-fork requests are classified. Instead ``requested_capabilities`` is
wrapped to ADD fork families on top of upstream's answer:

* upstream found an actionable request -> add each fork family it names;
* upstream found nothing -> add fork families only when the request also leads
  with an action (upstream's gate, or one of the fork's own verbs below);
* a request to schedule future work ("tasks") is left exactly as classified;
  the task's run is sealed from its prompt later, which reaches this again.

``register()`` mutates upstream state in place (every consumer reads it at call
time) and is idempotent. ``src.turn_contract`` calls it once at the end of the
module, before ``src.agent_runtime.authority`` imports the wrapped function.
"""

import re

FORK_FAMILY_TOOLS = {
    "health": frozenset({"manage_health"}),
    "pings": frozenset({"send_ping"}),
    "gallery": frozenset({"manage_gallery"}),
    "library_files": frozenset({"search_files", "manage_files"}),
}

FORK_FAMILY_WORDS = {
    "health": (
        r"\b(?:health|habits?|meals?|calories|kcal|macros?|protein|weight|weigh(?:ed)?|"
        r"workouts?|training|exercises?|steps|sleep|hydration|bmi|manage_health)\b"
    ),
    "pings": (
        r"\b(?:ping|notify|alert)\s+me\b|"
        r"\bsend\s+(?:me\s+)?(?:a\s+)?(?:ping|push(?:\s+notification)?|notification)\b|"
        r"\b(?:ntfy|send_ping)\b"
    ),
    # Possessive / explicit only: "latest video", "snap a photo" and "has X
    # uploaded?" are web and YouTube requests upstream routes elsewhere.
    "gallery": (
        r"\bgallery\b|\balbums?\b|"
        r"\b(?:my|our)\s+(?:own\s+)?(?:photos?|pictures?|pics|videos?|images?)\b|"
        r"\bmanage_gallery\b"
    ),
    "library_files": (
        r"\b(?:my|our)\s+(?:own\s+)?(?:uploads?|uploaded\s+files?|files|pdfs?|attachments?|e?books?)\b|"
        r"\b(?:i|we)\s+uploaded\b|\b(?:search_files|manage_files)\b"
    ),
}

# Upstream already serves these gallery requests itself (UI navigation, its
# app_api /api/gallery/library read, image edit/generation); don't add the
# fork's gallery tool on top of them.
_GALLERY_HANDLED_UPSTREAM = frozenset({"ui", "cookbook_admin", "image_editing", "image_generation"})

# Verbs the fork tools exist for: tracking ("log my weight 70kg") and
# notifying ("notify me to stretch"). Upstream's action gate does not list them.
# Only a LEADING verb counts, and a fork family word must still match.
_FORK_ACTION_LEAD = re.compile(
    r"^\s*(?:(?:please|ok(?:ay)?|also|then|now)[\s,!:-]+)*"
    r"(?:log|record|track|notify|ping|alert)\b",
    re.I,
)

_FORK_WORDS = {family: re.compile(pattern, re.I) for family, pattern in FORK_FAMILY_WORDS.items()}


def fork_families(text: str, upstream_families) -> frozenset:
    """The fork families to add to upstream's classification of ``text``."""
    upstream_families = frozenset(upstream_families or ())
    if "tasks" in upstream_families:
        return frozenset()
    named = {family for family, pattern in _FORK_WORDS.items() if pattern.search(text)}
    if upstream_families & _GALLERY_HANDLED_UPSTREAM:
        named.discard("gallery")
    if not named:
        return frozenset()
    if not upstream_families:
        from src import turn_contract

        if not (_FORK_ACTION_LEAD.search(text) or turn_contract._has_action_signal(text)):
            return frozenset()
    return frozenset(named)


def register() -> None:
    from src import turn_contract

    turn_contract.FAMILY_TOOLS.update(FORK_FAMILY_TOOLS)

    upstream_capabilities = turn_contract.requested_capabilities
    if getattr(upstream_capabilities, "_fork_wrapped", False):
        return

    def requested_capabilities(message, history=(), **kwargs):
        families = upstream_capabilities(message, history, **kwargs)
        extra = fork_families(str(message or ""), families)
        if not extra:
            return families
        # "unknown" means upstream found no family; a fork family is one.
        return (frozenset(families) - {"unknown"}) | extra

    requested_capabilities._fork_wrapped = True
    requested_capabilities.__wrapped__ = upstream_capabilities
    requested_capabilities.__doc__ = upstream_capabilities.__doc__
    turn_contract.requested_capabilities = requested_capabilities
