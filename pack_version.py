import re

from packaging.version import InvalidVersion, Version


############################################################
# Pack version parsing (legacy semver-style and "<mc>-<release>" scheme)
#
# The MC-prefixed scheme embeds the Minecraft version in the modpack version:
# "26.2-1.0", "26.2-1.6", pre-releases "26.2-1.0-beta.1". The release part
# resets for every Minecraft version. Everything here is shape-driven — it
# works on any version string regardless of the mc_prefixed_versions setting
# and never raises, so malformed versions cannot crash the workflow.

# Pre-release rank ordering: dev < alpha < beta < rc < final.
_PRE_RANKS = {"dev": 0, "a": 1, "alpha": 1, "b": 2, "beta": 2, "rc": 3}
_FINAL_PRE = (9, 0)

# "4.1.1a" -> post-release 1 (a=1, b=2, ...), mirroring the changelog
# factory's historical normalize_version behavior.
_LETTER_SUFFIX_RE = re.compile(r"^(\d+\.\d+\.\d+)([a-zA-Z])$")

# Trailing pre-release tag on an MC-scheme version: "26.2-1.0-beta.1".
_PRE_TAG_RE = re.compile(
    r"^(?P<base>.+?)[-_.](?P<tag>alpha|beta|rc)[.\-_]?(?P<num>\d+)?$", re.IGNORECASE
)

# Full-match for the MC-prefixed scheme (optionally with a pre-release tag).
_MC_SCHEME_RE = re.compile(
    r"^\d+(\.\d+)*-\d+(\.\d+)*(-(alpha|beta|rc)\.?\d*)?$", re.IGNORECASE
)

_UNPARSEABLE_KEY_TAIL = ((), (), _FINAL_PRE, 0)


def is_mc_prefixed_version(version_str) -> bool:
    """Return True when the version follows the "<mc>-<release>" scheme."""
    return bool(_MC_SCHEME_RE.fullmatch(str(version_str or "").strip()))


def _dotted_int_tuple(text):
    """Parse "26.2" -> (26, 2); return None when any component is not a number."""
    parts = str(text).split(".")
    if all(part.isdigit() for part in parts):
        return tuple(int(part) for part in parts)
    return None


def _split_pre_tag(text):
    """Split a trailing pre-release tag; returns (base, (rank, num))."""
    match = _PRE_TAG_RE.match(text)
    if not match:
        return text, _FINAL_PRE
    rank = _PRE_RANKS[match.group("tag").lower()]
    number = int(match.group("num") or 0)
    return match.group("base"), (rank, number)


def parse_pack_version_key(version_str) -> tuple:
    """Return a total-order sort key for any modpack version string. Never raises.

    Key shape: ``(kind, main, release, pre, post, raw_lower)`` where kind 1 is
    numeric-comparable (legacy PEP 440 or MC-scheme) and kind 0 is the
    deterministic fallback for unparseable strings (sorts below all numerics).
    For legacy versions ``main`` is the PEP 440 release tuple; for MC-scheme
    versions ``main`` is the Minecraft part and ``release`` the per-MC release.
    """
    raw = str(version_str or "").strip()
    lowered = raw.lower()
    # An empty string simply falls through to the unparseable fallback below.

    # Legacy branch: PEP 440 semantics, with the historical letter-suffix rule.
    candidate = raw
    letter_match = _LETTER_SUFFIX_RE.match(raw)
    if letter_match:
        post_number = ord(letter_match.group(2).lower()) - ord("a") + 1
        candidate = f"{letter_match.group(1)}.post{post_number}"
    try:
        parsed = Version(candidate)
    except InvalidVersion:
        parsed = None
    if parsed is not None:
        if parsed.pre is not None:
            pre = (_PRE_RANKS.get(parsed.pre[0], 3), parsed.pre[1])
        elif parsed.dev is not None:
            pre = (0, parsed.dev)
        else:
            pre = _FINAL_PRE
        return (1, parsed.release, (), pre, parsed.post or 0, lowered)

    # MC-scheme branch: "<mc>-<release>" with optional "-beta.N" style tag.
    base, pre = _split_pre_tag(raw)
    mc_text, separator, release_text = base.partition("-")
    if separator:
        mc_tuple = _dotted_int_tuple(mc_text)
        release_tuple = _dotted_int_tuple(release_text)
        if mc_tuple is not None and release_tuple is not None:
            return (1, mc_tuple, release_tuple, pre, 0, lowered)

    return (0, *_UNPARSEABLE_KEY_TAIL, lowered)


def is_prerelease(version_str) -> bool:
    """True when the version carries a pre-release tag (dev/alpha/beta/rc).

    Uses the parsed pre-release rank, so it recognizes ``rc``/``dev`` that a
    plain ``"beta"/"alpha" in version`` substring test would miss.
    """
    return parse_pack_version_key(version_str)[3] != _FINAL_PRE


def format_version_anchor(version_str) -> str:
    """Return the changelog heading/anchor text for a version.

    MC-scheme versions are used as-is; legacy versions keep the historical
    rule of prepending "v" unless the string already contains one.
    """
    version = str(version_str or "")
    if is_mc_prefixed_version(version):
        return version
    return version if "v" in version else f"v{version}"


def suggest_next_release(current_version):
    """Suggest the next MC-scheme version, or None for non-scheme versions.

    A pre-release promotes to its stable base ("26.2-1.0-beta.1" -> "26.2-1.0");
    otherwise the last numeric of the release part increments
    ("26.2-1.6" -> "26.2-1.7").
    """
    current = str(current_version or "").strip()
    if not is_mc_prefixed_version(current):
        return None
    base, pre = _split_pre_tag(current)
    if pre != _FINAL_PRE:
        return base
    mc_text, _, release_text = base.partition("-")
    release_parts = release_text.split(".")
    release_parts[-1] = str(int(release_parts[-1]) + 1)
    return f"{mc_text}-{'.'.join(release_parts)}"


def suggest_migration_version(target_mc) -> str:
    """Return the first MC-scheme version for a new Minecraft version."""
    return f"{str(target_mc or '').strip()}-1.0"
