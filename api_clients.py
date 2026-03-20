import json
import os
import re
import toml
import requests


MODRINTH_API_BASE = "https://api.modrinth.com/v2"
CURSEFORGE_API_BASE = "https://www.curseforge.com/api/v1"

SUPPORTED_MOD_LOADERS = ("fabric", "quilt", "forge", "neoforge")
MOD_LOADER_LABELS = {
    "fabric": "Fabric",
    "quilt": "Quilt",
    "forge": "Forge",
    "neoforge": "NeoForge",
}

# --- Mod loader utilities ---

def is_supported_mod_loader(loader_name):
    """Return True if loader_name is one of the recognised mod loaders."""
    return str(loader_name or "").strip().lower() in SUPPORTED_MOD_LOADERS


def normalize_mod_loader_name(loader_name, default="fabric"):
    """Return a canonical lowercase loader name, falling back to default (or "fabric") if unrecognised."""
    normalized = str(loader_name or "").strip().lower()
    if normalized in SUPPORTED_MOD_LOADERS:
        return normalized
    fallback = str(default or "fabric").strip().lower()
    return fallback if fallback in SUPPORTED_MOD_LOADERS else "fabric"


def get_mod_loader_label(loader_name):
    """Return the display label for a loader (e.g. "NeoForge"), defaulting to "Fabric"."""
    raw_value = str(loader_name or "").strip()
    normalized = raw_value.lower()
    if normalized in MOD_LOADER_LABELS:
        return MOD_LOADER_LABELS[normalized]
    if raw_value:
        return raw_value
    return MOD_LOADER_LABELS["fabric"]


def detect_active_mod_loader(versions_dict):
    """Return the first loader in SUPPORTED_MOD_LOADERS that has a non-empty version string, or "fabric"."""
    versions = versions_dict or {}
    for loader_name in SUPPORTED_MOD_LOADERS:
        if str(versions.get(loader_name, "")).strip():
            return loader_name
    return "fabric"


def get_pack_mod_loader_details(pack_toml):
    """Return (active_loader, active_loader_version) from a pack.toml dict."""
    versions = (pack_toml or {}).get("versions", {}) or {}
    active_loader = detect_active_mod_loader(versions)
    active_loader_version = str(versions.get(active_loader, "")).strip()
    return active_loader, active_loader_version


# --- Modrinth API ---

def infer_release_channel_from_metadata(mod_toml):
    """Guess the release channel ("alpha", "beta", or "release") from the filename and download URL."""
    metadata = " ".join(
        [
            str(mod_toml.get("filename", "")),
            str(mod_toml.get("download", {}).get("url", "")),
        ]
    ).lower()
    if "alpha" in metadata:
        return "alpha"
    if "beta" in metadata:
        return "beta"
    return "release"


def fetch_modrinth_version_by_id(version_id, version_cache):
    """Fetch a single Modrinth version payload by ID, using version_cache to avoid duplicate requests.

    Args:
        version_id: Modrinth version ID string.
        version_cache: Dict used as a mutable in-process cache (keyed by version ID).

    Returns:
        The parsed JSON payload dict, or None on failure or missing ID.
    """
    version_id = str(version_id or "").strip()
    if not version_id:
        return None
    if version_id in version_cache:
        return version_cache[version_id]

    try:
        response = requests.get(f"{MODRINTH_API_BASE}/version/{version_id}", timeout=20)
        response.raise_for_status()
        version_cache[version_id] = response.json()
    except Exception as ex:
        print(f"[Update] Failed to fetch Modrinth version '{version_id}': {ex}")
        version_cache[version_id] = None

    return version_cache[version_id]


def fetch_modrinth_project_versions(project_id, game_versions, loaders, project_versions_cache):
    """Fetch all Modrinth versions for a project filtered by game versions and loaders.

    Args:
        project_id: Modrinth project ID string.
        game_versions: List of Minecraft version strings to filter by.
        loaders: List of loader name strings to filter by.
        project_versions_cache: Dict used as a mutable in-process cache.

    Returns:
        List of version payload dicts, or [] on failure or missing ID.
    """
    project_id = str(project_id or "").strip()
    if not project_id:
        return []

    cache_key = (project_id, tuple(game_versions), tuple(loaders))
    if cache_key in project_versions_cache:
        return project_versions_cache[cache_key]

    versions = []
    try:
        response = requests.get(
            f"{MODRINTH_API_BASE}/project/{project_id}/version",
            params={
                "loaders": json.dumps(loaders),
                "game_versions": json.dumps(game_versions),
            },
            timeout=20,
        )
        response.raise_for_status()
        versions = response.json()
    except Exception as ex:
        print(f"[Update] Failed to fetch Modrinth versions for project '{project_id}': {ex}")

    project_versions_cache[cache_key] = versions
    return versions


def get_modrinth_version_type(mod_toml, version_cache):
    """Return the release channel for a mod's installed Modrinth version, falling back to metadata inference."""
    version_id = mod_toml.get("update", {}).get("modrinth", {}).get("version")
    version_payload = fetch_modrinth_version_by_id(version_id, version_cache)
    if version_payload:
        version_type = str(version_payload.get("version_type", "")).lower().strip()
        if version_type in ("release", "beta", "alpha"):
            return version_type
    return infer_release_channel_from_metadata(mod_toml)


def get_allowed_update_channels(current_channel):
    """Return the set of version types that are acceptable updates given the current channel.

    Alpha mods may update to any channel; all others are restricted to release and beta.
    """
    if str(current_channel).lower() == "alpha":
        return {"release", "beta", "alpha"}
    return {"release", "beta"}


def get_alpha_update_policy(settings):
    """Return the normalised alpha update policy ("always_skip", "always_allow", or "prompt") from settings."""
    raw_policy = str(getattr(settings, "alpha_update_policy", "prompt")).strip().lower()
    if raw_policy in ("always_skip", "skip", "never"):
        return "always_skip"
    if raw_policy in ("always_allow", "allow"):
        return "always_allow"
    return "prompt"


def should_keep_alpha_update(mod_name, current_channel, settings, log_prefix="[Update]"):
    """Return True if an alpha update should be applied, prompting the user when the policy is "prompt"."""
    policy = get_alpha_update_policy(settings)
    if policy == "always_skip":
        return False
    if policy == "always_allow":
        return True

    answer = input(
        f"{log_prefix} '{mod_name}' has only an alpha update available (current: {current_channel}). Allow alpha update? [N]: "
    ).strip()
    return answer in ("y", "Y", "yes", "Yes")


def select_latest_allowed_modrinth_version(project_id, current_channel, game_versions, loaders, project_versions_cache):
    """Return the newest Modrinth version payload whose type is permitted by current_channel, or None."""
    allowed_channels = get_allowed_update_channels(current_channel)
    versions = fetch_modrinth_project_versions(project_id, game_versions, loaders, project_versions_cache)
    for version_payload in versions:
        version_type = str(version_payload.get("version_type", "")).lower().strip()
        if version_type in allowed_channels:
            return version_payload
    return None


def select_modrinth_primary_file(version_payload):
    """Return the primary file entry from a Modrinth version payload, falling back to the first file."""
    files = list(version_payload.get("files", []) or [])
    if not files:
        return None
    for file_payload in files:
        if bool(file_payload.get("primary", False)):
            return file_payload
    return files[0]


def apply_modrinth_version_to_mod_toml(mod_toml, version_payload):
    """Write a Modrinth version's file metadata (URL, hash, filename, version ID) into mod_toml in-place.

    Returns:
        True if all required fields were present and mod_toml was updated; False otherwise.
    """
    target_version_id = str(version_payload.get("id", "")).strip()
    if not target_version_id:
        return False

    primary_file = select_modrinth_primary_file(version_payload)
    if not primary_file:
        return False

    hashes = primary_file.get("hashes", {}) or {}
    hash_format = ""
    hash_value = ""
    if "sha512" in hashes:
        hash_format = "sha512"
        hash_value = str(hashes["sha512"])
    elif "sha1" in hashes:
        hash_format = "sha1"
        hash_value = str(hashes["sha1"])

    download_url = str(primary_file.get("url", "")).strip()
    filename = str(primary_file.get("filename", "")).strip()
    if not download_url or not filename or not hash_format or not hash_value:
        return False

    mod_toml["filename"] = filename
    mod_toml.setdefault("download", {})
    mod_toml["download"]["url"] = download_url
    mod_toml["download"]["hash-format"] = hash_format
    mod_toml["download"]["hash"] = hash_value
    mod_toml.setdefault("update", {}).setdefault("modrinth", {})
    mod_toml["update"]["modrinth"]["version"] = target_version_id
    return True


# --- Compatibility inference ---

def extract_loader_hints_from_metadata(metadata):
    """Parse a metadata string and return the set of loader names mentioned in it."""
    text = str(metadata or "").lower()
    hints = set()
    # Matches "fabric" as a standalone word — negative lookbehind/lookahead [a-z0-9]
    # prevents partial matches (e.g. "fabricmc" does not trigger this).
    if re.search(r"(?<![a-z0-9])fabric(?![a-z0-9])", text):
        hints.add("fabric")
    # Same word-boundary guard for "quilt".
    if re.search(r"(?<![a-z0-9])quilt(?![a-z0-9])", text):
        hints.add("quilt")
    # "neoforge" must be checked before "forge" so the longer token is consumed first.
    if re.search(r"(?<![a-z0-9])neoforge(?![a-z0-9])", text):
        hints.add("neoforge")
    # Only add "forge" when "neoforge" was not already matched, avoiding a double-hit
    # on strings like "neoforge-1.21" that contain the substring "forge".
    if re.search(r"(?<![a-z0-9])forge(?![a-z0-9])", text) and "neoforge" not in hints:
        hints.add("forge")
    return hints


def extract_minecraft_version_hints_from_metadata(metadata):
    """Parse a metadata string and return the set of Minecraft version strings found in it."""
    text = str(metadata or "").lower()
    version_hints = set()

    # Explicit Minecraft tokens (e.g. mc1.20.1, minecraft-1.21.1)
    # Captures the version number that follows "mc" or "minecraft" with an optional separator.
    # The version group matches major.minor or major.minor.patch (e.g. "1.20", "1.20.1").
    for version in re.findall(r"(?:mc|minecraft)[-_ +]?(1\.\d{1,2}(?:\.\d{1,2})?)", text):
        version_hints.add(str(version))

    # Common filename suffixes/prefixes (e.g. +1.20.1, -1.21.1, _1.21)
    # The character class 1[6-9]|2\d restricts matches to MC 1.16+ to avoid false positives
    # on unrelated version numbers (e.g. Java versions, mod API versions).
    # Negative lookbehind/lookahead (?<!\d) / (?!\d) prevents matching inside longer numbers.
    for version in re.findall(r"(?<!\d)(1\.(?:1[6-9]|2\d)(?:\.\d{1,2})?)(?!\d)", text):
        version_hints.add(str(version))

    return version_hints


def is_target_minecraft_compatible_with_hints(target_minecraft_version, version_hints):
    """Return True/False if hints confirm/deny MC compatibility, or None when no hints are available."""
    hints = set(version_hints or [])
    if not hints:
        return None

    target_version = str(target_minecraft_version or "").strip()
    target_minor = ".".join(target_version.split(".", 2)[:2])
    for hint in hints:
        hint_text = str(hint).strip()
        hint_minor = ".".join(hint_text.split(".", 2)[:2])
        if hint_text == target_version or hint_text == target_minor or hint_minor == target_minor:
            return True
    return False


def is_target_loader_compatible_with_hints(target_loader, loader_hints):
    """Return True/False if hints confirm/deny loader compatibility, or None when no hints are available.

    Quilt is treated as compatible with Fabric mods because Quilt can load Fabric mods.
    """
    hints = set(loader_hints or [])
    if not hints:
        return None

    normalized_target = normalize_mod_loader_name(target_loader, default="fabric")
    compatible_loader_hints = {
        "fabric": {"fabric"},
        "quilt": {"quilt", "fabric"},
        "forge": {"forge"},
        "neoforge": {"neoforge"},
    }.get(normalized_target, {normalized_target})
    return len(hints.intersection(compatible_loader_hints)) > 0


def resolve_target_compatibility(loader_compatible, minecraft_compatible):
    """Combine per-axis compatibility signals into a single three-state result.

    Each axis uses True (confirmed compatible), False (confirmed incompatible), or
    None (no information). The combined result follows these rules:

    - Either axis is False  → False  (one confirmed incompatibility is enough to reject)
    - Both axes are True    → True   (both axes positively confirmed)
    - One axis True, one None → True (confirmed on one axis, silent on the other is acceptable)
    - Both axes are None    → None   (no information at all; caller decides)

    Args:
        loader_compatible: Three-state result for loader axis.
        minecraft_compatible: Three-state result for Minecraft version axis.

    Returns:
        True, False, or None.
    """
    # A single confirmed incompatibility overrides everything else.
    if loader_compatible is False or minecraft_compatible is False:
        return False
    # Both axes positively confirmed.
    if loader_compatible is True and minecraft_compatible is True:
        return True
    # Loader confirmed, MC unknown — treat as compatible (MC version data may simply be absent).
    if loader_compatible is True and minecraft_compatible is None:
        return True
    # MC confirmed, loader unknown — treat as compatible (loader data may simply be absent).
    if loader_compatible is None and minecraft_compatible is True:
        return True
    # Both axes are None — not enough information to decide.
    return None


def infer_compatibility_from_metadata(mod_toml, target_minecraft_version, mod_loader):
    """Infer compatibility purely from the mod's filename and download URL without any API calls."""
    metadata = " ".join(
        [
            str(mod_toml.get("filename", "")),
            str(mod_toml.get("download", {}).get("url", "")),
        ]
    ).lower()

    loader_hints = extract_loader_hints_from_metadata(metadata)
    version_hints = extract_minecraft_version_hints_from_metadata(metadata)
    loader_compatible = is_target_loader_compatible_with_hints(mod_loader, loader_hints)
    minecraft_compatible = is_target_minecraft_compatible_with_hints(target_minecraft_version, version_hints)
    return resolve_target_compatibility(loader_compatible, minecraft_compatible)


def is_installed_modrinth_version_compatible(mod_toml, target_minecraft_version, mod_loader, version_cache):
    """Check whether the mod's currently installed Modrinth version supports the target MC version and loader.

    Returns:
        True/False if the Modrinth API confirms compatibility; None if the version ID is absent,
        the API call fails, or there is insufficient data.
    """
    try:
        modrinth_meta = mod_toml.get("update", {}).get("modrinth", {})
        version_id = str(modrinth_meta.get("version", "")).strip()
        if not version_id:
            return None

        version_payload = fetch_modrinth_version_by_id(version_id, version_cache)
        if not version_payload:
            return None

        target_loader = normalize_mod_loader_name(mod_loader, default="fabric")
        target_version = str(target_minecraft_version or "").strip()
        target_minor = ".".join(target_version.split(".", 2)[:2])

        payload_loaders = {str(loader).strip().lower() for loader in version_payload.get("loaders", []) if str(loader).strip()}
        payload_game_versions = {
            str(game_version).strip()
            for game_version in version_payload.get("game_versions", [])
            if str(game_version).strip()
        }

        loader_compatible = target_loader in payload_loaders if payload_loaders else None
        mc_compatible = None
        if payload_game_versions:
            mc_compatible = False
            for game_version in payload_game_versions:
                gv_minor = ".".join(game_version.split(".", 2)[:2])
                if game_version == target_version or game_version == target_minor or gv_minor == target_minor:
                    mc_compatible = True
                    break

        return resolve_target_compatibility(loader_compatible, mc_compatible)
    except Exception as ex:
        mod_name = mod_toml.get("name", "unknown mod")
        print(f"[Migration] Installed Modrinth version compatibility check failed for '{mod_name}': {ex}")
        return None


def has_modrinth_project_version_for_target(mod_toml, target_minecraft_version, mod_loader, project_versions_cache):
    """Return True if the Modrinth project has any version for the target MC version and loader, else False/None."""
    try:
        project_id = mod_toml.get("update", {}).get("modrinth", {}).get("mod-id")
        if not project_id:
            return None
        versions = fetch_modrinth_project_versions(
            project_id=project_id,
            game_versions=[str(target_minecraft_version)],
            loaders=[normalize_mod_loader_name(mod_loader, default="fabric")],
            project_versions_cache=project_versions_cache,
        )
        return len(versions) > 0
    except Exception as ex:
        mod_name = mod_toml.get("name", "unknown mod")
        print(f"[Migration] Modrinth compatibility check failed for '{mod_name}': {ex}")
        return None


# --- CurseForge API ---

def _get_curseforge_file_payload(file_id, project_id, file_cache):
    normalized_project_id = str(project_id or "").strip()
    normalized_file_id = str(file_id or "").strip()
    if not normalized_project_id or not normalized_file_id:
        return None

    cache_key = (normalized_project_id, normalized_file_id)
    if cache_key in file_cache:
        return file_cache[cache_key]

    payload = None
    try:
        response = requests.get(
            f"{CURSEFORGE_API_BASE}/mods/{normalized_project_id}/files/{normalized_file_id}",
            timeout=20,
        )
        if response.status_code == 404:
            file_cache[cache_key] = None
            return None
        response.raise_for_status()
        payload = (response.json() or {}).get("data")
    except Exception as ex:
        print(
            f"[Migration] Failed fetching CurseForge file metadata "
            f"(project {normalized_project_id}, file {normalized_file_id}): {ex}"
        )

    file_cache[cache_key] = payload
    return payload


def _get_curseforge_project_files(project_id, target_minecraft_version, project_files_cache):
    normalized_project_id = str(project_id or "").strip()
    target_version = str(target_minecraft_version or "").strip()
    if not normalized_project_id:
        return []

    cache_key = (normalized_project_id, target_version)
    if cache_key in project_files_cache:
        return project_files_cache[cache_key]

    files = []
    try:
        page_size = 50
        index = 0
        max_pages = 4
        for _ in range(max_pages):
            response = requests.get(
                f"{CURSEFORGE_API_BASE}/mods/{normalized_project_id}/files",
                params={
                    "index": index,
                    "pageSize": page_size,
                    "gameVersion": target_version,
                },
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json() or {}
            page_files = list(payload.get("data", []) or [])
            files.extend(page_files)

            pagination = payload.get("pagination", {}) or {}
            returned_page_size = int(pagination.get("pageSize", page_size) or page_size)
            total_count = int(pagination.get("totalCount", 0) or 0)
            index += returned_page_size if returned_page_size > 0 else page_size

            if not page_files:
                break
            if total_count and index >= total_count:
                break
            if len(page_files) < page_size and not total_count:
                break
    except Exception as ex:
        print(f"[Migration] Failed fetching CurseForge files for project '{normalized_project_id}': {ex}")

    project_files_cache[cache_key] = files
    return files


def fetch_modrinth_project_info(project_id, project_info_cache):
    """Fetch Modrinth project metadata (GET /v2/project/{id}).

    Returns the project dict, or None on failure/not-found.
    """
    key = str(project_id or "").strip()
    if not key:
        return None
    if key in project_info_cache:
        return project_info_cache[key]
    payload = None
    try:
        response = requests.get(f"{MODRINTH_API_BASE}/project/{key}", timeout=20)
        if response.status_code == 404:
            project_info_cache[key] = None
            return None
        response.raise_for_status()
        payload = response.json()
    except Exception:
        pass
    project_info_cache[key] = payload
    return payload


def fetch_curseforge_project_info(project_id, project_info_cache):
    """Fetch CurseForge project metadata (GET /v1/mods/{modId}).

    Returns the project dict, or None on failure/not-found.
    """
    key = str(project_id or "").strip()
    if not key:
        return None
    if key in project_info_cache:
        return project_info_cache[key]
    payload = None
    try:
        response = requests.get(f"{CURSEFORGE_API_BASE}/mods/{key}", timeout=20)
        if response.status_code == 404:
            project_info_cache[key] = None
            return None
        response.raise_for_status()
        payload = (response.json() or {}).get("data")
    except Exception:
        pass
    project_info_cache[key] = payload
    return payload


def is_mod_classified_as_library(mod_toml, mr_project_info_cache, cf_project_info_cache):
    """Return True if Modrinth or CurseForge classifies this mod as a library, else False or None.

    Modrinth: checks for "library" in the project's categories list.
    CurseForge: checks for a category whose name or slug contains "library".
    Returns None if no project info could be fetched.
    """
    mr_id = str(mod_toml.get("update", {}).get("modrinth", {}).get("mod-id", "")).strip()
    if mr_id:
        info = fetch_modrinth_project_info(mr_id, mr_project_info_cache)
        if info is not None:
            return "library" in [str(c).lower() for c in info.get("categories", [])]

    cf_id = str(mod_toml.get("update", {}).get("curseforge", {}).get("project-id", "")).strip()
    if cf_id:
        info = fetch_curseforge_project_info(cf_id, cf_project_info_cache)
        if info is not None:
            for cat in info.get("categories", []):
                if "library" in str(cat.get("name", "")).lower() or "library" in str(cat.get("slug", "")).lower():
                    return True
            return False

    return None


def _evaluate_curseforge_file_compatibility(file_payload, target_minecraft_version, mod_loader):
    if not file_payload:
        return None

    game_versions = [str(entry).strip() for entry in (file_payload.get("gameVersions", []) or []) if str(entry).strip()]
    metadata = " ".join(
        [
            str(file_payload.get("fileName", "")),
            str(file_payload.get("displayName", "")),
            " ".join(game_versions),
        ]
    ).lower()

    loader_hints = extract_loader_hints_from_metadata(metadata)
    version_hints = set(extract_minecraft_version_hints_from_metadata(metadata))
    for game_version in game_versions:
        lower_version = game_version.lower()
        if re.fullmatch(r"1\.(?:1[6-9]|2\d)(?:\.\d{1,2})?", lower_version):
            version_hints.add(game_version)

    loader_compatible = is_target_loader_compatible_with_hints(mod_loader, loader_hints)
    minecraft_compatible = is_target_minecraft_compatible_with_hints(target_minecraft_version, version_hints)
    return resolve_target_compatibility(loader_compatible, minecraft_compatible)


def is_installed_curseforge_file_compatible(
    mod_toml,
    target_minecraft_version,
    mod_loader,
    curseforge_file_cache,
):
    """Check whether the mod's currently installed CurseForge file supports the target MC version and loader.

    Returns:
        True/False if compatibility can be determined from the file metadata; None otherwise.
    """
    try:
        curseforge_meta = mod_toml.get("update", {}).get("curseforge", {})
        project_id = curseforge_meta.get("project-id")
        file_id = curseforge_meta.get("file-id")
        if not project_id or not file_id:
            return None

        file_payload = _get_curseforge_file_payload(file_id, project_id, curseforge_file_cache)
        return _evaluate_curseforge_file_compatibility(file_payload, target_minecraft_version, mod_loader)
    except Exception as ex:
        mod_name = mod_toml.get("name", "unknown mod")
        print(f"[Migration] Installed CurseForge file compatibility check failed for '{mod_name}': {ex}")
        return None


def has_curseforge_project_version_for_target(
    mod_toml,
    target_minecraft_version,
    mod_loader,
    curseforge_project_files_cache,
):
    """Return True if any CurseForge file for the project is compatible with the target, else False/None.

    Iterates all fetched project files and short-circuits as soon as a confirmed-compatible file is
    found. Returns None only when every file returned an inconclusive result.
    """
    try:
        curseforge_meta = mod_toml.get("update", {}).get("curseforge", {})
        project_id = curseforge_meta.get("project-id")
        if not project_id:
            return None

        project_files = _get_curseforge_project_files(
            project_id=project_id,
            target_minecraft_version=target_minecraft_version,
            project_files_cache=curseforge_project_files_cache,
        )
        if not project_files:
            return False

        saw_explicit_false = False
        saw_explicit_true = False
        for file_payload in project_files:
            file_compatibility = _evaluate_curseforge_file_compatibility(
                file_payload,
                target_minecraft_version,
                mod_loader,
            )
            if file_compatibility is True:
                saw_explicit_true = True
                break
            if file_compatibility is False:
                saw_explicit_false = True

        if saw_explicit_true:
            return True
        if saw_explicit_false:
            return False
        return None
    except Exception as ex:
        mod_name = mod_toml.get("name", "unknown mod")
        print(f"[Migration] CurseForge project compatibility check failed for '{mod_name}': {ex}")
        return None


# --- Compatibility determination ---

def determine_mod_target_compatibility(
    mod_toml,
    target_minecraft_version,
    mod_loader,
    version_cache,
    project_versions_cache,
    curseforge_file_cache,
    curseforge_project_files_cache,
):
    """Determine mod compatibility with the target MC version and loader using a prioritised fallback chain.

    Sources are tried in order: installed Modrinth version, Modrinth project versions,
    installed CurseForge file, CurseForge project files, and finally filename/URL metadata.
    The first source that returns a non-None result wins.

    Returns:
        Tuple of (compatibility, source_label) where compatibility is True, False, or None,
        and source_label is a string identifying which source produced the result.
    """
    compatibility = is_installed_modrinth_version_compatible(
        mod_toml,
        target_minecraft_version,
        mod_loader,
        version_cache,
    )
    if compatibility is not None:
        return compatibility, "installed_modrinth_version"

    compatibility = has_modrinth_project_version_for_target(
        mod_toml,
        target_minecraft_version,
        mod_loader,
        project_versions_cache,
    )
    if compatibility is not None:
        return compatibility, "project_modrinth_versions"

    compatibility = is_installed_curseforge_file_compatible(
        mod_toml,
        target_minecraft_version,
        mod_loader,
        curseforge_file_cache,
    )
    if compatibility is not None:
        return compatibility, "installed_curseforge_file"

    compatibility = has_curseforge_project_version_for_target(
        mod_toml,
        target_minecraft_version,
        mod_loader,
        curseforge_project_files_cache,
    )
    if compatibility is not None:
        return compatibility, "project_curseforge_versions"

    compatibility = infer_compatibility_from_metadata(
        mod_toml,
        target_minecraft_version,
        mod_loader,
    )
    if compatibility is not None:
        return compatibility, "metadata_inference"

    return None, "unknown"


def select_modrinth_replacement_version_for_target(
    project_id,
    mod_name,
    current_channel,
    target_minecraft_version,
    mod_loader,
    project_versions_cache,
    settings,
):
    """Find the best Modrinth version to migrate a mod to the target MC version and loader.

    Prefers the latest version within the allowed channels; if none qualify and the only
    available version is alpha, prompts (or defers to policy) before returning it.

    Returns:
        A Modrinth version payload dict, or None if no suitable version exists.
    """
    target_game_versions = [str(target_minecraft_version)]
    target_loaders = [normalize_mod_loader_name(mod_loader, default="fabric")]

    replacement_version = select_latest_allowed_modrinth_version(
        project_id=project_id,
        current_channel=current_channel,
        game_versions=target_game_versions,
        loaders=target_loaders,
        project_versions_cache=project_versions_cache,
    )
    if replacement_version:
        return replacement_version

    project_versions = fetch_modrinth_project_versions(
        project_id=project_id,
        game_versions=target_game_versions,
        loaders=target_loaders,
        project_versions_cache=project_versions_cache,
    )
    if not project_versions:
        return None

    latest_any = project_versions[0]
    latest_any_type = str(latest_any.get("version_type", "")).strip().lower()
    if latest_any_type == "alpha" and str(current_channel).lower() != "alpha":
        if not should_keep_alpha_update(mod_name, current_channel, settings, log_prefix="[Migration]"):
            return None
    return latest_any


def try_retarget_modrinth_mod_to_target(
    item_path,
    mod_toml,
    target_minecraft_version,
    mod_loader,
    version_cache,
    project_versions_cache,
    settings,
):
    """Attempt to update a Modrinth-tracked mod's .toml file to a version compatible with the target.

    Finds the best replacement version, applies it to mod_toml, and writes the result back to
    item_path. Does nothing if the mod is already on the best available version.

    Returns:
        Tuple of (success: bool, version_label: str). version_label is empty on failure.
    """
    modrinth_meta = mod_toml.get("update", {}).get("modrinth", {})
    project_id = str(modrinth_meta.get("mod-id", "")).strip()
    current_version_id = str(modrinth_meta.get("version", "")).strip()
    if not project_id:
        return False, ""

    mod_name = str(mod_toml.get("name", os.path.basename(item_path)))
    current_channel = get_modrinth_version_type(mod_toml, version_cache)
    replacement_version = select_modrinth_replacement_version_for_target(
        project_id=project_id,
        mod_name=mod_name,
        current_channel=current_channel,
        target_minecraft_version=target_minecraft_version,
        mod_loader=mod_loader,
        project_versions_cache=project_versions_cache,
        settings=settings,
    )
    replacement_version_id = str((replacement_version or {}).get("id", "")).strip()
    if not replacement_version or not replacement_version_id:
        return False, ""
    if current_version_id and replacement_version_id == current_version_id:
        return False, ""

    if not apply_modrinth_version_to_mod_toml(mod_toml, replacement_version):
        return False, ""

    with open(item_path, "w", encoding="utf8") as f:
        toml.dump(mod_toml, f)

    replacement_label = str(
        replacement_version.get("version_number")
        or replacement_version.get("name")
        or replacement_version_id
    )
    return True, replacement_label
