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
    return str(loader_name or "").strip().lower() in SUPPORTED_MOD_LOADERS


def normalize_mod_loader_name(loader_name, default="fabric"):
    normalized = str(loader_name or "").strip().lower()
    if normalized in SUPPORTED_MOD_LOADERS:
        return normalized
    fallback = str(default or "fabric").strip().lower()
    return fallback if fallback in SUPPORTED_MOD_LOADERS else "fabric"


def get_mod_loader_label(loader_name):
    raw_value = str(loader_name or "").strip()
    normalized = raw_value.lower()
    if normalized in MOD_LOADER_LABELS:
        return MOD_LOADER_LABELS[normalized]
    if raw_value:
        return raw_value
    return MOD_LOADER_LABELS["fabric"]


def detect_active_mod_loader(versions_dict):
    versions = versions_dict or {}
    for loader_name in SUPPORTED_MOD_LOADERS:
        if str(versions.get(loader_name, "")).strip():
            return loader_name
    return "fabric"


def get_pack_mod_loader_details(pack_toml):
    versions = (pack_toml or {}).get("versions", {}) or {}
    active_loader = detect_active_mod_loader(versions)
    active_loader_version = str(versions.get(active_loader, "")).strip()
    return active_loader, active_loader_version


# --- Modrinth API ---

def infer_release_channel_from_metadata(mod_toml):
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
    version_id = mod_toml.get("update", {}).get("modrinth", {}).get("version")
    version_payload = fetch_modrinth_version_by_id(version_id, version_cache)
    if version_payload:
        version_type = str(version_payload.get("version_type", "")).lower().strip()
        if version_type in ("release", "beta", "alpha"):
            return version_type
    return infer_release_channel_from_metadata(mod_toml)


def get_allowed_update_channels(current_channel):
    if str(current_channel).lower() == "alpha":
        return {"release", "beta", "alpha"}
    return {"release", "beta"}


def get_alpha_update_policy(settings):
    raw_policy = str(getattr(settings, "alpha_update_policy", "prompt")).strip().lower()
    if raw_policy in ("always_skip", "skip", "never"):
        return "always_skip"
    if raw_policy in ("always_allow", "allow"):
        return "always_allow"
    return "prompt"


def should_keep_alpha_update(mod_name, current_channel, settings, log_prefix="[Update]"):
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
    allowed_channels = get_allowed_update_channels(current_channel)
    versions = fetch_modrinth_project_versions(project_id, game_versions, loaders, project_versions_cache)
    for version_payload in versions:
        version_type = str(version_payload.get("version_type", "")).lower().strip()
        if version_type in allowed_channels:
            return version_payload
    return None


def select_modrinth_primary_file(version_payload):
    files = list(version_payload.get("files", []) or [])
    if not files:
        return None
    for file_payload in files:
        if bool(file_payload.get("primary", False)):
            return file_payload
    return files[0]


def apply_modrinth_version_to_mod_toml(mod_toml, version_payload):
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
    text = str(metadata or "").lower()
    hints = set()
    if re.search(r"(?<![a-z0-9])fabric(?![a-z0-9])", text):
        hints.add("fabric")
    if re.search(r"(?<![a-z0-9])quilt(?![a-z0-9])", text):
        hints.add("quilt")
    if re.search(r"(?<![a-z0-9])neoforge(?![a-z0-9])", text):
        hints.add("neoforge")
    if re.search(r"(?<![a-z0-9])forge(?![a-z0-9])", text) and "neoforge" not in hints:
        hints.add("forge")
    return hints


def extract_minecraft_version_hints_from_metadata(metadata):
    text = str(metadata or "").lower()
    version_hints = set()

    # Explicit Minecraft tokens (e.g. mc1.20.1, minecraft-1.21.1)
    for version in re.findall(r"(?:mc|minecraft)[-_ +]?(1\.\d{1,2}(?:\.\d{1,2})?)", text):
        version_hints.add(str(version))

    # Common filename suffixes/prefixes (e.g. +1.20.1, -1.21.1, _1.21)
    for version in re.findall(r"(?<!\d)(1\.(?:1[6-9]|2\d)(?:\.\d{1,2})?)(?!\d)", text):
        version_hints.add(str(version))

    return version_hints


def is_target_minecraft_compatible_with_hints(target_minecraft_version, version_hints):
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
    if loader_compatible is False or minecraft_compatible is False:
        return False
    if loader_compatible is True and minecraft_compatible is True:
        return True
    if loader_compatible is True and minecraft_compatible is None:
        return True
    if loader_compatible is None and minecraft_compatible is True:
        return True
    return None


def infer_compatibility_from_metadata(mod_toml, target_minecraft_version, mod_loader):
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
