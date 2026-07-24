launch_message = """
▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
█                           █
█    HaXr's Modpack Tool    █
█                           █
▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀"""

import os
import json
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed
from shutil import rmtree, make_archive, move, copytree

import toml  # pip install toml

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import LiteralScalarString

from mdutils.mdutils import MdUtils

# Settings
from settings import load_settings
from api_clients import configure_curseforge_api_key
import pack_manager

# Standalone-tool config and per-project paths
from tool_config import (
    load_tool_config,
    save_tool_config,
    register_project,
    remove_project,
    find_project,
    resolve_packwiz_exe,
    resolve_github_token,
    resolve_curseforge_key,
)
from project_context import (
    LEGACY_TOOL_DIR_NAME,
    compute_project_paths,
    validate_project_root,
    read_pack_manifest,
    preflight_project,
    normalize_drag_drop_path,
    prompt_yes,
)
from api_clients import (
    SUPPORTED_MOD_LOADERS,
    is_supported_mod_loader,
    normalize_mod_loader_name,
    get_mod_loader_label,
    get_pack_mod_loader_details,
    fetch_modrinth_version_by_id,
    fetch_modrinth_project_versions,
    get_modrinth_version_type,
    get_alpha_update_policy,
    should_keep_alpha_update,
    select_latest_allowed_modrinth_version,
    apply_modrinth_version_to_mod_toml,
    _get_curseforge_file_payload,
    determine_mod_target_compatibility,
    try_retarget_modrinth_mod_to_target,
    is_mod_classified_as_library,
)

from changelog_helpers import (
    initialize_modlist_label_index,
    maybe_generate_update_overview,
    maybe_generate_config_changes,
    uses_llm_config_changes,
)

# GitHub Download
from github_downloader import AsyncGitHubDownloader
import asyncio

# Changelog stuff
from changelog_factory import ChangelogFactory, changelog_filename, changelog_stem

# Version scheme helpers
from pack_version import (
    is_mc_prefixed_version,
    format_version_anchor,
    suggest_next_release,
    suggest_migration_version,
)

# Markdown Stuff
import markdown_helper as markdown

# Semver stuff
from packaging.version import Version, InvalidVersion

############################################################
# YAML (ruamel only) — one instance used everywhere

yaml = YAML()  # round-trip by default (typ="rt")
yaml.indent(mapping=2, sequence=4, offset=2)
yaml.default_flow_style = False
yaml.preserve_quotes = True  # harmless, helps if you ever edit existing YAML

############################################################
# Variables

# Tool install location — independent of any modpack project.
script_path = os.path.abspath(__file__)  # Absolute path to the script
tool_dir = os.path.dirname(script_path)
packwiz_manifest = "pack.toml"

# Project-scoped globals. None until activate_project() binds them to the
# active modpack project; rebound on every project switch.
tool_cfg = None
git_path = None  # Active project root (historical name kept for call sites)
packwiz_path = None
serverpack_path = None
packwiz_exe_path = None
bcc_client_config_path = None
bcc_server_config_path = None
export_path = None
tempfolder_path = None
temp_mods_path = None
settings_path = None
prev_release = None
changelog_dir_path = None
tempgit_path = None
mods_path = None
crash_assistant_config_path = None
crash_assistant_markdown_path = None
settings = None
changelog_factory = None
pack_version = None
modpack_name = None
minecraft_version = None
active_mod_loader = None
mod_loader_version = None

############################################################
# Functions

def determine_server_export():
    """Determine whether the server pack should be exported or not and return a boolean."""
    return settings.export_server and input("Want to export server pack? [N]: ") in ("y", "Y", "yes", "Yes")


def prompt_new_pack_version(context: str = "", suggested: str = None) -> str:
    """Prompt for the next modpack version.

    With a ``suggested`` default, Enter returns the suggestion. Otherwise the
    current version is shown and Enter returns an empty string, which callers
    treat as "keep the current version".
    """
    default = suggested or pack_version
    entered = input(f"New modpack version{context} [{default}]: ").strip()
    return entered or suggested or ""


def ensure_migration_targets(settings):
    """Prompt for any missing migration targets and the post-migration pack version.

    Loader inputs are validated against the supported loaders. The chosen pack
    version lands in ``settings.bump_target_version`` (empty keeps the current
    version).

    Args:
        settings: The loaded Settings object; target fields are populated in-place.
    """
    if not settings.migration_target_minecraft:
        settings.migration_target_minecraft = input("Target Minecraft version for migration: ").strip()
    if not settings.migration_target_minecraft:
        raise ValueError("Migration selected but no target Minecraft version was provided.")

    pack_manifest_path = os.path.join(packwiz_path, packwiz_manifest)
    current_loader = "fabric"
    current_loader_version = ""
    try:
        with open(pack_manifest_path, "r", encoding="utf8") as f:
            local_pack_toml = toml.load(f)
        current_loader, current_loader_version = get_pack_mod_loader_details(local_pack_toml)
    except Exception as ex:
        print(f"[Migration] Failed reading current loader from pack.toml: {ex}")

    default_target_loader = normalize_mod_loader_name(
        settings.migration_target_mod_loader or current_loader
    )
    loader_prompt = (
        f"Target modloader [fabric/quilt/forge/neoforge] [{default_target_loader}]: "
    )
    target_loader_input = input(loader_prompt).strip().lower()
    if target_loader_input:
        if not is_supported_mod_loader(target_loader_input):
            raise ValueError(
                f"Unsupported target modloader '{target_loader_input}'. "
                "Use fabric/quilt/forge/neoforge."
            )
        settings.migration_target_mod_loader = target_loader_input
    else:
        settings.migration_target_mod_loader = default_target_loader

    target_loader = normalize_mod_loader_name(settings.migration_target_mod_loader, default=current_loader)
    default_loader_version = str(settings.migration_target_mod_loader_version or "").strip()
    if not default_loader_version and target_loader == "fabric":
        default_loader_version = str(settings.migration_target_fabric or "").strip()
    if not default_loader_version and target_loader == current_loader:
        default_loader_version = current_loader_version

    loader_version_prompt = f"Target {get_mod_loader_label(target_loader)} version [{default_loader_version}]: "
    target_loader_version = input(loader_version_prompt).strip()
    if target_loader_version:
        settings.migration_target_mod_loader_version = target_loader_version
    elif not settings.migration_target_mod_loader_version:
        settings.migration_target_mod_loader_version = default_loader_version

    # Backward compatibility: keep existing Fabric-specific setting populated when needed.
    if target_loader == "fabric":
        if settings.migration_target_mod_loader_version:
            settings.migration_target_fabric = settings.migration_target_mod_loader_version
        elif settings.migration_target_fabric:
            settings.migration_target_mod_loader_version = settings.migration_target_fabric

    if target_loader != current_loader and not str(settings.migration_target_mod_loader_version).strip():
        raise ValueError(
            f"Switching loaders requires a target {get_mod_loader_label(target_loader)} version."
        )

    # Keep compatibility check loader aligned with the target loader unless explicitly overridden.
    configured_compat_loader = str(settings.migration_mod_loader or "").strip().lower()
    if (
        not configured_compat_loader
        or configured_compat_loader == current_loader
        or (configured_compat_loader == "fabric" and target_loader != "fabric")
    ):
        settings.migration_mod_loader = target_loader
    elif not is_supported_mod_loader(configured_compat_loader):
        settings.migration_mod_loader = target_loader

    suggested = (
        suggest_migration_version(settings.migration_target_minecraft, pack_version)
        if settings.mc_prefixed_versions else None
    )
    settings.bump_target_version = prompt_new_pack_version(" after migration", suggested=suggested)


def get_config_changes_mode_label(settings) -> str:
    """Return a human-readable label describing the active config-changes generation mode."""
    if uses_llm_config_changes(settings):
        model = str(settings.auto_config_model).strip() or "default-model"
        return f"LLM ({model})"
    provider = str(settings.auto_config_provider).strip() or "unknown"
    return f"Fallback (deterministic, provider '{provider}' unsupported)"


def configure_actions_via_menu(settings):
    """Display the interactive action menu and apply the chosen workflow to settings.

    Returns:
        False if the user selects exit (choice 0), True otherwise.
    """
    def prompt_changelog_autogen_overwrite(force_prompt=False):
        should_prompt_overview = force_prompt or settings.auto_generate_update_overview or settings.generate_update_summary_only
        if should_prompt_overview:
            answer = input("Override existing 'Update overview' for this run? [Y]: ").strip()
            if answer:
                settings.auto_summary_overwrite_existing = answer.lower() in ("y", "yes")
            else:
                settings.auto_summary_overwrite_existing = True

        should_prompt_config = force_prompt or settings.auto_generate_config_changes
        if should_prompt_config:
            answer = input("Override existing 'Config Changes' for this run? [Y]: ").strip()
            if answer:
                settings.auto_config_overwrite_existing = answer.lower() in ("y", "yes")
            else:
                settings.auto_config_overwrite_existing = True

    # Load the configured export_client once to avoid reading settings.yml twice.
    with open(settings_path, "r", encoding="utf-8") as s_file:
        _configured_settings_yml = yaml.load(s_file) or {}
    configured_export_client = bool(_configured_settings_yml.get("export_client", False))

    config_mode_label = get_config_changes_mode_label(settings)

    print(
        f"""
Active project: {modpack_name} ({git_path}) | v{pack_version} MC {minecraft_version}

Choose action:
1) Run configured workflow (settings.yml)
2) Migration only
3) Export client only
4) Export server only
5) Migration + export client
6) Migration + export client + server
7) Refresh only
8) Update mods only
9) Bump modpack version only
10) Clear stored repository data
11) Generate changelog summary only
12) List disabled mods
13) Add mod
14) Find orphaned library mods
P) Switch / manage projects
0) Exit

Config Changes generator: {config_mode_label}
"""
    )

    choice = input("Selection [1]: ").strip() or "1"

    if choice == "0":
        return False

    if choice.lower() == "p":
        return "switch_project"

    if choice not in {str(n) for n in range(1, 15)}:
        print(f"Unknown choice '{choice}'. Falling back to configured workflow.")
        choice = "1"

    # Reset runtime flow toggles before applying chosen mode.
    settings.refresh_only = False
    settings.update_mods_only = False
    settings.bump_version_only = False
    settings.clear_repo_data_only = False
    settings.generate_update_summary_only = False
    settings.list_disabled_mods_only = False
    settings.add_mod_only = False
    settings.find_orphaned_libraries_only = False
    settings.migrate_minecraft_version = False
    settings.export_client = False
    settings.export_server = False
    settings.bump_target_version = ""

    if choice == "1":
        # Keep the configured export_client value while preserving existing server prompt behavior.
        settings.export_client = configured_export_client
        settings.export_server = determine_server_export()
        prompt_changelog_autogen_overwrite()
        return True

    if choice == "2":
        settings.migrate_minecraft_version = True
        ensure_migration_targets(settings)
        prompt_changelog_autogen_overwrite()
        return True

    if choice == "3":
        settings.export_client = True
        prompt_changelog_autogen_overwrite()
        return True

    if choice == "4":
        settings.export_server = True
        prompt_changelog_autogen_overwrite()
        return True

    if choice == "5":
        settings.migrate_minecraft_version = True
        settings.export_client = True
        ensure_migration_targets(settings)
        prompt_changelog_autogen_overwrite()
        return True

    if choice == "6":
        settings.migrate_minecraft_version = True
        settings.export_client = True
        settings.export_server = True
        ensure_migration_targets(settings)
        prompt_changelog_autogen_overwrite()
        return True

    if choice == "7":
        settings.refresh_only = True
        return True

    if choice == "8":
        settings.update_mods_only = True
        return True

    if choice == "9":
        settings.bump_version_only = True
        suggested = suggest_next_release(pack_version) if settings.mc_prefixed_versions else None
        settings.bump_target_version = prompt_new_pack_version(suggested=suggested) or pack_version
        return True

    if choice == "10":
        settings.clear_repo_data_only = True
        return True

    if choice == "11":
        settings.generate_update_summary_only = True
        prompt_changelog_autogen_overwrite(force_prompt=True)
        return True

    if choice == "12":
        settings.list_disabled_mods_only = True
        return True

    if choice == "13":
        settings.add_mod_only = True
        return True

    if choice == "14":
        settings.find_orphaned_libraries_only = True
        return True

    # Unreachable: the validation above maps unknown choices to "1".
    return True


def parse_active_projects(input_path, parse_object):
    """Parse pw.toml files and return names of active projects as a list."""
    active_project = []
    for mod_toml in os.listdir(input_path):
        mod_toml_path = os.path.join(input_path, mod_toml)
        try:
            if os.path.isfile(mod_toml_path): # Checks if mod_toml_path is a file.
                with open(mod_toml_path, "r", encoding="utf8") as f:
                    mod_toml = toml.load(f)
                    side = str(mod_toml['side'])
                    if side in ("both", "client", "server"):
                        mod_name = markdown.remove_bracketed_text(mod_toml[parse_object])
                        if side == "both":
                            active_project.append(mod_name)
                        else:
                            active_project.append(f"{mod_name} [{side.capitalize()}]")
        except Exception as ex:
            print(ex, mod_toml)
    return active_project


def parse_filenames_as_json(input_path):
    """Parse pw.toml files and return 'filename' values as a JSON list."""
    filenames = []
    for mod_toml in os.listdir(input_path):
        mod_toml_path = os.path.join(input_path, mod_toml)
        try:
            if os.path.isfile(mod_toml_path):
                with open(mod_toml_path, "r", encoding="utf8") as f:
                    mod_toml = toml.load(f)
                    side = str(mod_toml['side'])
                    if 'filename' in mod_toml and side in ("both", "client", "server"):
                        filenames.append(mod_toml['filename'])
        except Exception as ex:
            print(f"Error processing file {mod_toml}: {ex}")
    filenames.sort(key=lambda x: x.lower())
    return json.dumps(filenames, indent=2)


def build_combined_modlist_markdown(input_path, include_side_tags=True):
    """Build a Markdown document listing active and inactive mods from pw.toml files.

    Args:
        input_path: Directory containing the mod .toml files.
        include_side_tags: When True, appends a [Both/Client/Server] tag to each name.

    Returns:
        A Markdown string with ## Active Mods and ## Inactive Mods sections.
    """
    active_mods = []
    inactive_mods = []

    for mod_toml in sorted(os.listdir(input_path), key=lambda item: item.lower()):
        mod_toml_path = os.path.join(input_path, mod_toml)
        try:
            if not os.path.isfile(mod_toml_path) or not mod_toml.endswith(".toml"):
                continue

            with open(mod_toml_path, "r", encoding="utf8") as f:
                mod_data = toml.load(f)

            mod_name = markdown.remove_bracketed_text(str(mod_data.get("name", mod_toml)))
            side_value = str(mod_data.get("side", "both")).strip()
            side_base = side_value.split("(", 1)[0].strip().lower() or "both"
            side_label = "Both" if side_base == "both" else side_base.capitalize()

            formatted_name = f"{mod_name} [{side_label}]" if include_side_tags else mod_name
            if "disabled" in side_value.lower():
                inactive_mods.append(formatted_name)
            else:
                active_mods.append(formatted_name)
        except Exception as ex:
            print(f"Error processing file {mod_toml}: {ex}")

    lines = ["# Mod List", ""]
    lines.append("## Active Mods")
    if active_mods:
        lines.extend([f"- {mod_name}" for mod_name in active_mods])
    else:
        lines.append("- None")

    lines.append("")
    lines.append("## Inactive Mods")
    if inactive_mods:
        lines.extend([f"- {mod_name}" for mod_name in inactive_mods])
    else:
        lines.append("- None")

    lines.append("")
    return "\n".join(lines)


def list_disabled_mods():
    """Print all disabled mods to stdout and return them as a list of (name, filename, side) tuples."""
    disabled_mods = []
    os.chdir(mods_path)
    for item in sorted(os.listdir()):
        item_path = os.path.join(mods_path, item)
        if not os.path.isfile(item_path) or not item.endswith(".toml"):
            continue
        try:
            with open(item_path, "r", encoding="utf8") as f:
                mod_toml = toml.load(f)
            side_value = str(mod_toml.get("side", "both"))
            if "disabled" in side_value.lower():
                mod_name = mod_toml.get("name", item)
                disabled_mods.append((mod_name, item, side_value))
        except Exception as ex:
            print(f"[Mods] Failed to inspect '{item}': {ex}")
    os.chdir(packwiz_path)

    print(f"[Mods] Disabled mods: {len(disabled_mods)}")
    for mod_name, mod_file, side_value in disabled_mods:
        print(f"- {mod_name} ({mod_file}, side={side_value})")

    return disabled_mods


def find_and_remove_orphaned_library_mods():
    """Scan active mods for library mods with no active dependants and let the user remove them."""
    os.chdir(mods_path)
    toml_files = [
        f for f in sorted(os.listdir())
        if os.path.isfile(os.path.join(mods_path, f)) and f.endswith(".toml")
    ]
    active_mods = []
    for f in toml_files:
        try:
            with open(os.path.join(mods_path, f), "r", encoding="utf8") as fh:
                t = toml.load(fh)
            if "disabled" not in str(t.get("side", "both")):
                active_mods.append((f, t))
        except Exception:
            pass

    total = len(active_mods)
    print(f"[LibScan] Step 1/{total}: Fetching dependency data for {total} mods...", flush=True)

    # Build the set of project IDs that are required dependencies of any active mod.
    # Fetches are done in parallel to minimise API wait time.
    version_cache = {}
    curseforge_file_cache = {}
    required_cf_ids = set()
    required_mr_ids = set()

    def _fetch_deps(item, mod_toml):
        cf_deps = set()
        mr_deps = set()
        cf_meta = mod_toml.get("update", {}).get("curseforge", {})
        cf_file_id = str(cf_meta.get("file-id", "")).strip()
        cf_proj_id = str(cf_meta.get("project-id", "")).strip()
        if cf_file_id and cf_proj_id:
            payload = _get_curseforge_file_payload(cf_file_id, cf_proj_id, curseforge_file_cache)
            if payload:
                for dep in payload.get("dependencies", []):
                    if dep.get("relationType") == 3:
                        dep_id = str(dep.get("modId", "")).strip()
                        if dep_id:
                            cf_deps.add(dep_id)
        mr_version_id = str(mod_toml.get("update", {}).get("modrinth", {}).get("version", "")).strip()
        if mr_version_id:
            version_payload = fetch_modrinth_version_by_id(mr_version_id, version_cache)
            if version_payload:
                for dep in version_payload.get("dependencies", []):
                    if str(dep.get("dependency_type", "")).lower() == "required":
                        dep_id = str(dep.get("project_id", "")).strip()
                        if dep_id:
                            mr_deps.add(dep_id)
        return mod_toml.get("name", item), cf_deps, mr_deps

    completed = 0
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = {executor.submit(_fetch_deps, item, mod_toml): None for item, mod_toml in active_mods}
        for future in as_completed(futures):
            completed += 1
            mod_name, cf_deps, mr_deps = future.result()
            required_cf_ids.update(cf_deps)
            required_mr_ids.update(mr_deps)
            print(f"[LibScan] [{completed}/{total}] {mod_name}", flush=True)

    # Find active mods that are not depended on by anything and are classified as libraries.
    candidates = [
        (item, mod_toml) for item, mod_toml in active_mods
        if str(mod_toml.get("update", {}).get("curseforge", {}).get("project-id", "")).strip() not in required_cf_ids
        and str(mod_toml.get("update", {}).get("modrinth", {}).get("mod-id", "")).strip() not in required_mr_ids
    ]
    num_candidates = len(candidates)
    print(f"\n[LibScan] Step 2: Checking {num_candidates} mods with no dependants for library classification...", flush=True)
    mr_project_info_cache = {}
    cf_project_info_cache = {}
    orphaned_libraries = []

    def _classify(item, mod_toml):
        mod_name = mod_toml.get("name", item)
        is_lib = is_mod_classified_as_library(mod_toml, mr_project_info_cache, cf_project_info_cache)
        return item, mod_name, is_lib

    completed2 = 0
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures2 = {executor.submit(_classify, item, mod_toml): None for item, mod_toml in candidates}
        for future in as_completed(futures2):
            completed2 += 1
            item, mod_name, is_lib = future.result()
            print(f"[LibScan] [{completed2}/{num_candidates}] Checking {mod_name}...", flush=True)
            if is_lib:
                orphaned_libraries.append((mod_name, item))

    if not orphaned_libraries:
        print("[LibScan] No orphaned library mods found.")
        os.chdir(packwiz_path)
        return

    print(f"\n[LibScan] Found {len(orphaned_libraries)} orphaned library mod(s):")
    for i, (mod_name, _) in enumerate(orphaned_libraries, 1):
        print(f"  {i}) {mod_name}")

    print()
    choice = input("Remove all [A], select individually [S], or cancel [Enter]: ").strip().lower()
    if not choice:
        print("[LibScan] Cancelled.")
        os.chdir(packwiz_path)
        return

    to_remove = []
    if choice == "a":
        to_remove = list(orphaned_libraries)
    elif choice == "s":
        for mod_name, item in orphaned_libraries:
            ans = input(f"  Remove {mod_name}? [y/N]: ").strip().lower()
            if ans in ("y", "yes"):
                to_remove.append((mod_name, item))

    if not to_remove:
        print("[LibScan] Nothing to remove.")
        os.chdir(packwiz_path)
        return

    os.chdir(packwiz_path)
    removed = []
    for mod_name, item in to_remove:
        try:
            pack_manager.remove_mod(packwiz_exe_path, packwiz_path, mods_path, item)
            removed.append(mod_name)
            print(f"[LibScan] Removed: {mod_name}", flush=True)
        except Exception as ex:
            print(f"[LibScan] Failed to remove {mod_name}: {ex}")

    if removed:
        print(f"[LibScan] Removed {len(removed)} orphaned library mod(s).")


def add_mod_via_prompt():
    """Interactively prompt for a mod source and identifier, then add it via packwiz.

    Returns:
        True if the mod was added successfully, False otherwise.
    """
    print("[PackWiz] Add mod")
    source_input = input("Source [modrinth(mr)/curseforge(cf)/url] [modrinth]: ").strip().lower() or "modrinth"
    source_aliases = {
        "mr": "modrinth",
        "modrinth": "modrinth",
        "cf": "curseforge",
        "curseforge": "curseforge",
        "url": "url",
    }
    source = source_aliases.get(source_input)
    if source is None:
        print(f"[PackWiz] Invalid source '{source_input}'.")
        return False

    if source == "url":
        add_value = input("Paste project/version URL: ").strip()
    else:
        add_value = input(f"Enter {source} project slug/id: ").strip()

    if not add_value:
        print("[PackWiz] No value provided. Skipping add.")
        return False

    try:
        if source == "modrinth":
            pack_manager.add_mod_from_modrinth(packwiz_exe_path, packwiz_path, add_value)
        elif source == "curseforge":
            pack_manager.add_mod_from_curseforge(packwiz_exe_path, packwiz_path, add_value)
        elif source == "url":
            pack_manager.add_mod_from_url(packwiz_exe_path, packwiz_path, mods_path, add_value)
    except Exception as ex:
        print(f"[PackWiz] Add command failed: {ex}")
        return False

    print(f"[PackWiz] Added from {source}: {add_value}")
    return True


def is_version_in_range(input_version, min_version=None, max_version=None, include_min=True, include_max=True):
    """Return True if input_version falls within [min_version, max_version].

    Args:
        input_version: The version string to test.
        min_version: Lower bound (inclusive by default). None means no lower bound.
        max_version: Upper bound (inclusive by default). None means no upper bound.
        include_min: Whether the lower bound is inclusive.
        include_max: Whether the upper bound is inclusive.

    Returns:
        True if in range, False otherwise.

    Raises:
        ValueError: If any version string is not a valid PEP 440 version.
    """
    try:
        input_ver = Version(input_version)
        if min_version is not None:
            min_ver = Version(min_version)
            if (input_ver < min_ver) or (input_ver == min_ver and not include_min):
                return False
        if max_version is not None:
            max_ver = Version(max_version)
            if (input_ver > max_ver) or (input_ver == max_ver and not include_max):
                return False
        return True
    except InvalidVersion as e:
        raise ValueError(f"Invalid version provided: {e}")


def resolve_comparison_packwiz_root(input_version, tag_mc_ver):
    """Resolve the Packwiz root folder path for a given changelog version.

    When versioned roots are enabled in settings the path is derived from
    the configured pattern; otherwise "Packwiz" is returned.

    Args:
        input_version: The modpack version string of the comparison snapshot.
        tag_mc_ver: The Minecraft version string associated with that snapshot.

    Returns:
        A relative path string such as "Packwiz" or "Packwiz/1.21.1".
    """
    if not settings.comparison_files_use_versioned_packwiz_root:
        return "Packwiz"

    # MC-scheme versions cannot be compared against the PEP 440 range bounds.
    if is_mc_prefixed_version(input_version):
        return "Packwiz"

    min_version = str(settings.comparison_files_versioned_root_min_version or "").strip() or None
    max_version = str(settings.comparison_files_versioned_root_max_version or "").strip() or None

    try:
        if not is_version_in_range(input_version, min_version, max_version):
            return "Packwiz"
    except ValueError as ex:
        print(f"[Settings] Invalid comparison-files version range: {ex}. Falling back to default 'Packwiz' root.")
        return "Packwiz"

    pattern = str(settings.comparison_files_versioned_root_pattern or "").strip() or "Packwiz/{mc_version}"
    try:
        return pattern.format(version=input_version, mc_version=tag_mc_ver)
    except KeyError as ex:
        print(f"[Settings] Invalid comparison root pattern key {ex}. Falling back to 'Packwiz/{{mc_version}}'.")
        return f"Packwiz/{tag_mc_ver}"


def normalize_disabled_side(side_value):
    """Return a side string with a (disabled) suffix, preserving the base side value."""
    side_text = str(side_value).strip()
    if "disabled" in side_text:
        return side_text
    side_base = side_text.split("(", 1)[0].strip() or "both"
    return f"{side_base}(disabled)"


def normalize_enabled_side(side_value):
    """Return a side string with the (disabled) suffix stripped, leaving the base side value."""
    side_text = str(side_value).strip()
    if "disabled" not in side_text:
        return side_text or "both"
    side_base = side_text.split("(", 1)[0].strip() or "both"
    return side_base


def snapshot_mod_toml_content():
    """Read all mod .toml files and return a dict mapping filename to raw file content."""
    snapshot = {}
    os.chdir(mods_path)
    for item in sorted(os.listdir()):
        item_path = os.path.join(mods_path, item)
        if not os.path.isfile(item_path) or not item.endswith(".toml"):
            continue
        try:
            with open(item_path, "r", encoding="utf8") as f:
                snapshot[item] = f.read()
        except Exception as ex:
            print(f"[Update] Failed to snapshot '{item}': {ex}")
    os.chdir(packwiz_path)
    return snapshot


def find_updated_disabled_mods(previous_snapshot):
    """Return mods whose .toml changed since the snapshot and are currently disabled.

    Args:
        previous_snapshot: Dict of {filename: raw_content} from snapshot_mod_toml_content().

    Returns:
        List of (filename, mod_name) tuples for updated disabled mods.
    """
    updated_disabled_mods = []
    os.chdir(mods_path)
    for item in sorted(os.listdir()):
        item_path = os.path.join(mods_path, item)
        if not os.path.isfile(item_path) or not item.endswith(".toml"):
            continue
        try:
            with open(item_path, "r", encoding="utf8") as f:
                current_content = f.read()
            if previous_snapshot.get(item) == current_content:
                continue

            mod_toml = toml.loads(current_content)
            side_value = str(mod_toml.get("side", "both"))
            if "disabled" in side_value:
                updated_disabled_mods.append((item, mod_toml.get("name", item)))
        except Exception as ex:
            print(f"[Update] Failed to inspect '{item}' after update: {ex}")
    os.chdir(packwiz_path)
    return updated_disabled_mods


def enable_mods_by_files(mod_files):
    """Re-enable a list of disabled mods by stripping the (disabled) suffix from their side field.

    Args:
        mod_files: Iterable of .toml filenames (relative to mods_path) to enable.

    Returns:
        List of mod display names that were successfully re-enabled.
    """
    enabled_mods = []
    os.chdir(mods_path)
    for item in mod_files:
        item_path = os.path.join(mods_path, item)
        if not os.path.isfile(item_path) or not item.endswith(".toml"):
            continue
        try:
            with open(item_path, "r", encoding="utf8") as f:
                mod_toml = toml.load(f)

            side_value = str(mod_toml.get("side", "both"))
            if "disabled" not in side_value:
                continue

            mod_toml["side"] = normalize_enabled_side(side_value)
            with open(item_path, "w", encoding="utf8") as f:
                toml.dump(mod_toml, f)
            enabled_mods.append(mod_toml.get("name", item))
        except Exception as ex:
            print(f"[Update] Failed to re-enable '{item}': {ex}")

    os.chdir(packwiz_path)
    return enabled_mods


def get_pack_update_constraints():
    """Read pack.toml and return the current Minecraft version and active mod loaders.

    Returns:
        A tuple ([game_version], [loader, ...]) suitable for Modrinth version queries.
    """
    try:
        with open(os.path.join(packwiz_path, packwiz_manifest), "r", encoding="utf8") as f:
            local_pack_toml = toml.load(f)
    except Exception as ex:
        print(f"[Update] Failed to read pack manifest for update constraints: {ex}")
        fallback_loader = normalize_mod_loader_name(globals().get("active_mod_loader", "fabric"))
        return [str(minecraft_version)], [fallback_loader]

    versions = local_pack_toml.get("versions", {})
    game_version = str(versions.get("minecraft", minecraft_version))
    loaders = [loader for loader in SUPPORTED_MOD_LOADERS if versions.get(loader)]
    if not loaders:
        loaders = ["fabric"]
    return [game_version], loaders


def enforce_release_channel_policy(previous_snapshot, log_prefix="[Update]", allowed_alpha_mod_files=None):
    """Revert or redirect any mod update that landed on a disallowed alpha version.

    Compares the current .toml files against a pre-update snapshot. For each mod
    that moved to an alpha release channel, the update is either rolled back to the
    previous version or redirected to the latest non-alpha version, according to
    the alpha update policy in settings.

    Args:
        previous_snapshot: Dict of {filename: raw_content} captured before the update run.
        log_prefix: String prefix for console log messages.
        allowed_alpha_mod_files: Set of filenames exempt from alpha enforcement.

    Returns:
        Dict with keys "blocked_alpha" (list of mod names reverted) and
        "retargeted" (list of (mod_name, target_label) tuples redirected).
    """
    if not previous_snapshot:
        return {"blocked_alpha": [], "retargeted": []}

    blocked_alpha = []
    retargeted = []
    allowed_alpha_mod_files = set(allowed_alpha_mod_files or [])
    version_cache = {}
    project_versions_cache = {}
    game_versions, loaders = get_pack_update_constraints()

    os.chdir(mods_path)
    for item in sorted(os.listdir()):
        item_path = os.path.join(mods_path, item)
        if not os.path.isfile(item_path) or not item.endswith(".toml"):
            continue

        previous_content = previous_snapshot.get(item)
        if previous_content is None:
            continue

        try:
            with open(item_path, "r", encoding="utf8") as f:
                current_content = f.read()
            if current_content == previous_content:
                continue

            previous_toml = toml.loads(previous_content)
            current_toml = toml.loads(current_content)
        except Exception as ex:
            print(f"{log_prefix} Failed to inspect '{item}' for release-channel enforcement: {ex}")
            continue

        mod_name = str(current_toml.get("name", previous_toml.get("name", item)))
        previous_modrinth_meta = previous_toml.get("update", {}).get("modrinth", {})
        current_modrinth_meta = current_toml.get("update", {}).get("modrinth", {})
        project_id = current_modrinth_meta.get("mod-id") or previous_modrinth_meta.get("mod-id")
        previous_version_id = str(previous_modrinth_meta.get("version", "")).strip()
        current_version_id = str(current_modrinth_meta.get("version", "")).strip()

        if not project_id or not previous_version_id or not current_version_id or previous_version_id == current_version_id:
            continue

        previous_channel = get_modrinth_version_type(previous_toml, version_cache)
        current_channel = get_modrinth_version_type(current_toml, version_cache)
        if previous_channel == "alpha" or current_channel != "alpha":
            continue
        if item in allowed_alpha_mod_files:
            continue
        if should_keep_alpha_update(mod_name, previous_channel, settings, log_prefix=log_prefix):
            continue

        replacement_version = select_latest_allowed_modrinth_version(
            project_id=project_id,
            current_channel=previous_channel,
            game_versions=game_versions,
            loaders=loaders,
            project_versions_cache=project_versions_cache,
        )
        replacement_version_id = str((replacement_version or {}).get("id", "")).strip()

        if (
            replacement_version
            and replacement_version_id
            and replacement_version_id != current_version_id
            and replacement_version_id != previous_version_id
            and apply_modrinth_version_to_mod_toml(current_toml, replacement_version)
        ):
            try:
                with open(item_path, "w", encoding="utf8") as f:
                    toml.dump(current_toml, f)
                target_label = str(
                    replacement_version.get("version_number")
                    or replacement_version.get("name")
                    or replacement_version_id
                )
                retargeted.append((mod_name, target_label))
                continue
            except Exception as ex:
                print(f"{log_prefix} Failed to retarget '{mod_name}' to non-alpha version: {ex}")

        try:
            with open(item_path, "w", encoding="utf8") as f:
                f.write(previous_content)
            blocked_alpha.append(mod_name)
        except Exception as ex:
            print(f"{log_prefix} Failed to rollback disallowed alpha update for '{mod_name}': {ex}")

    os.chdir(packwiz_path)

    if blocked_alpha:
        print(f"{log_prefix} Blocked {len(blocked_alpha)} disallowed alpha updates.")
        print(f"{log_prefix} Reverted: {', '.join(blocked_alpha)}")
    if retargeted:
        retargeted_labels = [f"{name} -> {target}" for name, target in retargeted]
        print(f"{log_prefix} Redirected {len(retargeted)} updates to beta/release versions.")
        print(f"{log_prefix} Redirected: {', '.join(retargeted_labels)}")

    return {"blocked_alpha": blocked_alpha, "retargeted": retargeted}


def find_pinned_mods_with_available_updates():
    """Scan pinned mods and return those that have a newer version available on Modrinth.

    Returns:
        List of candidate dicts with keys: file, name, current_version_id,
        latest_version_id, latest_version_label, latest_version_type,
        requires_alpha_consent.
    """
    update_candidates = []
    game_versions, loaders = get_pack_update_constraints()
    version_cache = {}
    project_versions_cache = {}

    os.chdir(mods_path)
    for item in sorted(os.listdir()):
        item_path = os.path.join(mods_path, item)
        if not os.path.isfile(item_path) or not item.endswith(".toml"):
            continue

        try:
            with open(item_path, "r", encoding="utf8") as f:
                mod_toml = toml.load(f)

            if not bool(mod_toml.get("pin", False)):
                continue

            mod_name = str(mod_toml.get("name", item))
            modrinth_meta = mod_toml.get("update", {}).get("modrinth", {})
            project_id = modrinth_meta.get("mod-id")
            current_version_id = modrinth_meta.get("version")

            if not project_id or not current_version_id:
                print(f"[Update] Skipping pinned mod '{mod_name}' (unsupported update source).")
                continue

            current_channel = get_modrinth_version_type(mod_toml, version_cache)
            project_versions = fetch_modrinth_project_versions(
                project_id=project_id,
                game_versions=game_versions,
                loaders=loaders,
                project_versions_cache=project_versions_cache,
            )
            if not project_versions:
                continue

            latest_any = project_versions[0]
            latest_any_id = str(latest_any.get("id", ""))
            latest_any_type = str(latest_any.get("version_type", "")).strip().lower()
            latest_allowed = select_latest_allowed_modrinth_version(
                project_id=project_id,
                current_channel=current_channel,
                game_versions=game_versions,
                loaders=loaders,
                project_versions_cache=project_versions_cache,
            )
            if latest_allowed:
                latest_version_id = str(latest_allowed.get("id", ""))
                if latest_version_id and latest_version_id != str(current_version_id):
                    update_candidates.append(
                        {
                            "file": item,
                            "name": mod_name,
                            "current_version_id": str(current_version_id),
                            "latest_version_id": latest_version_id,
                            "latest_version_label": str(
                                latest_allowed.get("version_number") or latest_allowed.get("name") or latest_version_id
                            ),
                            "latest_version_type": str(latest_allowed.get("version_type", "")).strip().lower(),
                            "requires_alpha_consent": False,
                        }
                    )
                continue

            if (
                latest_any_id
                and latest_any_id != str(current_version_id)
                and latest_any_type == "alpha"
                and current_channel != "alpha"
                and get_alpha_update_policy(settings) != "always_skip"
            ):
                update_candidates.append(
                    {
                        "file": item,
                        "name": mod_name,
                        "current_version_id": str(current_version_id),
                        "latest_version_id": latest_any_id,
                        "latest_version_label": str(
                            latest_any.get("version_number") or latest_any.get("name") or latest_any_id
                        ),
                        "latest_version_type": latest_any_type,
                        "requires_alpha_consent": True,
                    }
                )
        except Exception as ex:
            print(f"[Update] Failed to check pinned mod '{item}': {ex}")

    os.chdir(packwiz_path)
    return update_candidates


def prompt_for_pinned_mod_updates(update_candidates):
    """Ask the user which pinned mods they want to update and collect consent for alpha versions.

    Args:
        update_candidates: List of candidate dicts from find_pinned_mods_with_available_updates().

    Returns:
        Tuple (selected_files, approved_alpha_files) of filename lists.
    """
    selected_files = []
    approved_alpha_files = []
    for candidate in update_candidates:
        mod_name = candidate["name"]
        latest_label = candidate["latest_version_label"]
        latest_type = str(candidate.get("latest_version_type", "")).strip()
        latest_type_text = f", {latest_type}" if latest_type else ""
        if bool(candidate.get("requires_alpha_consent", False)):
            answer = input(
                f"[PackWiz] Pinned mod '{mod_name}' only has an alpha update available ({latest_label}{latest_type_text}). Update it and keep it pinned? [N]: "
            ).strip()
        else:
            answer = input(
                f"[PackWiz] Pinned mod '{mod_name}' has an update available ({latest_label}{latest_type_text}). Update it and keep it pinned? [N]: "
            ).strip()
        if answer in ("y", "Y", "yes", "Yes"):
            selected_files.append(candidate["file"])
            if bool(candidate.get("requires_alpha_consent", False)):
                approved_alpha_files.append(candidate["file"])
    return selected_files, approved_alpha_files


def set_pin_state_for_mod_files(mod_files, should_pin):
    """Set or remove the pin flag on a list of mod .toml files.

    Args:
        mod_files: Iterable of .toml filenames to update.
        should_pin: True to add pin = true, False to remove the pin key.

    Returns:
        List of filenames that were successfully updated.
    """
    updated_files = []
    os.chdir(mods_path)
    for item in mod_files:
        item_path = os.path.join(mods_path, item)
        if not os.path.isfile(item_path) or not item.endswith(".toml"):
            continue
        try:
            with open(item_path, "r", encoding="utf8") as f:
                mod_toml = toml.load(f)

            if should_pin:
                mod_toml["pin"] = True
            else:
                mod_toml.pop("pin", None)

            with open(item_path, "w", encoding="utf8") as f:
                toml.dump(mod_toml, f)
            updated_files.append(item)
        except Exception as ex:
            print(f"[Update] Failed to set pin state for '{item}': {ex}")

    os.chdir(packwiz_path)
    return updated_files


def attempt_packwiz_targeted_mod_update(item, mod_toml):
    """Try to update a single mod to its latest compatible version.

    Args:
        item: The .toml filename (e.g. "sodium.pw.toml").
        mod_toml: Parsed TOML dict for the mod (mutated in-place on success).

    Returns:
        Tuple (success: bool, identifier: str) where identifier is the mod name on success.
    """
    item_path = os.path.join(mods_path, item)
    try:
        success = pack_manager.update_single_mod(packwiz_exe_path, packwiz_path, item_path)
    except Exception:
        success = False
    if success:
        return True, str(mod_toml.get("name", item))
    return False, ""


def try_find_replacement_for_incompatible_mod(
    item,
    item_path,
    mod_toml,
    target_minecraft_version,
    mod_loader,
    version_cache,
    project_versions_cache,
    curseforge_file_cache,
    curseforge_project_files_cache,
):
    """Attempt to retarget an incompatible mod to a compatible version.

    First tries Modrinth retargeting, then falls back to a packwiz update. The
    updated file is verified for target compatibility before reporting success.

    Args:
        item: The .toml filename.
        item_path: Absolute path to the .toml file.
        mod_toml: Parsed TOML dict for the mod.
        target_minecraft_version: Minecraft version being migrated to.
        mod_loader: Mod loader being migrated to.
        version_cache: Shared Modrinth version API response cache.
        project_versions_cache: Shared Modrinth project-versions API response cache.
        curseforge_file_cache: Shared CurseForge file API response cache.
        curseforge_project_files_cache: Shared CurseForge project-files API response cache.

    Returns:
        Tuple (replaced: bool, details: str) where details describes the replacement source.
    """
    try:
        replaced_with_modrinth, modrinth_target = try_retarget_modrinth_mod_to_target(
            item_path=item_path,
            mod_toml=mod_toml,
            target_minecraft_version=target_minecraft_version,
            mod_loader=mod_loader,
            version_cache=version_cache,
            project_versions_cache=project_versions_cache,
            settings=settings,
        )
        if replaced_with_modrinth:
            return True, f"Modrinth {modrinth_target}"
    except Exception as ex:
        mod_name = mod_toml.get("name", item)
        print(f"[Migration] Failed Modrinth retarget for '{mod_name}': {ex}")

    # Temporarily clear the pin flag so packwiz can update a pinned mod during migration.
    was_pinned = bool(mod_toml.get("pin", False))
    if was_pinned:
        unpinned = {k: v for k, v in mod_toml.items() if k != "pin"}
        try:
            with open(item_path, "w", encoding="utf8") as f:
                toml.dump(unpinned, f)
        except Exception:
            was_pinned = False  # couldn't write, skip restore logic

    updated_by_packwiz, packwiz_identifier = attempt_packwiz_targeted_mod_update(item, mod_toml)

    if not updated_by_packwiz:
        if was_pinned:
            # Restore original TOML (update failed, leave it pinned as before)
            try:
                with open(item_path, "w", encoding="utf8") as f:
                    toml.dump(mod_toml, f)
            except Exception:
                pass
        return False, ""

    try:
        with open(item_path, "r", encoding="utf8") as f:
            reloaded_toml = toml.load(f)
    except Exception:
        reloaded_toml = mod_toml

    compatibility, _ = determine_mod_target_compatibility(
        reloaded_toml,
        target_minecraft_version,
        mod_loader,
        version_cache,
        project_versions_cache,
        curseforge_file_cache,
        curseforge_project_files_cache,
    )
    if compatibility is False:
        return False, ""
    return True, f"Packwiz update ({packwiz_identifier})"


def disable_incompatible_mods(target_minecraft_version, mod_loader):
    """Check every active mod for compatibility with the migration target and disable those that are incompatible.

    For each incompatible mod a replacement is attempted first via
    try_find_replacement_for_incompatible_mod; if that fails the mod's side
    field is set to disabled.

    Args:
        target_minecraft_version: The Minecraft version being migrated to.
        mod_loader: The mod loader being migrated to.

    Returns:
        List of strings describing each disabled mod (name + compatibility source).
    """
    disabled_mods = []
    replaced_mods = []
    version_cache = {}
    project_versions_cache = {}
    curseforge_file_cache = {}
    curseforge_project_files_cache = {}
    os.chdir(mods_path)
    toml_files = [f for f in sorted(os.listdir()) if os.path.isfile(os.path.join(mods_path, f)) and f.endswith(".toml")]
    active_toml_files = []
    for f in toml_files:
        try:
            with open(os.path.join(mods_path, f), "r", encoding="utf8") as fh:
                t = toml.load(fh)
            if "disabled" not in str(t.get("side", "both")):
                active_toml_files.append((f, t))
        except Exception:
            pass
    print(f"[Migration] Checking {len(active_toml_files)} active mods for compatibility with {target_minecraft_version} ({mod_loader})...", flush=True)
    for i, (item, mod_toml) in enumerate(active_toml_files, 1):
        item_path = os.path.join(mods_path, item)
        try:
            side_value = str(mod_toml.get("side", "both"))
            mod_name = mod_toml.get("name", item)
            print(f"[Migration] [{i}/{len(active_toml_files)}] Checking {mod_name}...", flush=True)

            compatibility, compatibility_source = determine_mod_target_compatibility(
                mod_toml,
                target_minecraft_version,
                mod_loader,
                version_cache,
                project_versions_cache,
                curseforge_file_cache,
                curseforge_project_files_cache,
            )
            compatible = compatibility
            if compatible is None:
                # Preserve existing conservative behavior for genuinely unknown metadata.
                compatible = True

            if compatible:
                continue

            print(f"[Migration]   -> Incompatible ({compatibility_source}), attempting replacement...", flush=True)
            replacement_applied, replacement_details = try_find_replacement_for_incompatible_mod(
                item=item,
                item_path=item_path,
                mod_toml=mod_toml,
                target_minecraft_version=target_minecraft_version,
                mod_loader=mod_loader,
                version_cache=version_cache,
                project_versions_cache=project_versions_cache,
                curseforge_file_cache=curseforge_file_cache,
                curseforge_project_files_cache=curseforge_project_files_cache,
            )
            if replacement_applied:
                replaced_mods.append(f"{mod_name} -> {replacement_details}")
                print(f"[Migration]   -> Replaced via {replacement_details}", flush=True)
                continue

            mod_toml["side"] = normalize_disabled_side(side_value)
            with open(item_path, "w", encoding="utf8") as f:
                toml.dump(mod_toml, f)
            disabled_mods.append(f"{mod_name} ({compatibility_source})")
            print(f"[Migration]   -> Disabled", flush=True)
        except Exception as ex:
            print(f"[Migration] Failed to process '{item}': {ex}")

    if replaced_mods:
        print(f"[Migration] Retargeted {len(replaced_mods)} incompatible mods to target-compatible versions.")
        print("[Migration] Retargeted mods: " + ", ".join(replaced_mods))

    return disabled_mods


def migrate_minecraft_version(
    target_minecraft_version,
    target_fabric_version=None,
    target_mod_loader=None,
    target_mod_loader_version=None,
    update_all_mods=True,
    disable_outdated_mods=True,
    mod_loader=None,
):
    """Update pack.toml to a new Minecraft version and optionally update/disable mods.

    Args:
        target_minecraft_version: The Minecraft version to migrate to.
        target_fabric_version: Legacy Fabric version override (superseded by target_mod_loader_version).
        target_mod_loader: Target mod loader name; defaults to the current loader.
        target_mod_loader_version: Target loader version string.
        update_all_mods: When True, runs packwiz update --all after updating pack.toml.
        disable_outdated_mods: When True, calls disable_incompatible_mods after updating.
        mod_loader: Loader name used for compatibility checks; defaults to target loader.

    Returns:
        Tuple (minecraft_version, mod_loader, mod_loader_version) reflecting the final state.
    """
    os.chdir(packwiz_path)
    with open(packwiz_manifest, "r", encoding="utf8") as f:
        local_pack_toml = toml.load(f)

    versions = local_pack_toml.setdefault("versions", {})
    current_minecraft = str(versions.get("minecraft", minecraft_version))
    current_loader, current_loader_version = get_pack_mod_loader_details(local_pack_toml)

    if not target_minecraft_version:
        print("[Migration] migration_target_minecraft is empty. Skipping migration.")
        return current_minecraft, current_loader, current_loader_version

    resolved_target_loader = normalize_mod_loader_name(target_mod_loader or current_loader, default=current_loader)
    resolved_target_loader_version = str(target_mod_loader_version or "").strip()

    # Backward compatibility for existing Fabric-only setting.
    if not resolved_target_loader_version and resolved_target_loader == "fabric":
        resolved_target_loader_version = str(target_fabric_version or "").strip()

    if not resolved_target_loader_version:
        if resolved_target_loader == current_loader:
            resolved_target_loader_version = current_loader_version
        else:
            resolved_target_loader_version = str(versions.get(resolved_target_loader, "")).strip()

    if resolved_target_loader != current_loader and not resolved_target_loader_version:
        print(
            f"[Migration] Switching from {get_mod_loader_label(current_loader)} "
            f"to {get_mod_loader_label(resolved_target_loader)} requires a target loader version. Skipping migration."
        )
        return current_minecraft, current_loader, current_loader_version

    versions["minecraft"] = target_minecraft_version

    # Keep only one active loader entry to avoid ambiguous pack constraints.
    for loader_name in SUPPORTED_MOD_LOADERS:
        if loader_name != resolved_target_loader and loader_name in versions:
            versions.pop(loader_name, None)
    versions[resolved_target_loader] = resolved_target_loader_version

    with open(packwiz_manifest, "w", encoding="utf8") as f:
        toml.dump(local_pack_toml, f)

    print("[Migration] Running refresh...", flush=True)
    pack_manager.refresh_index(packwiz_exe_path, packwiz_path)
    if update_all_mods:
        previous_snapshot = snapshot_mod_toml_content()
        print("[Migration] Updating all mods (this can take a while)...", flush=True)
        pack_manager.update_all_mods(packwiz_exe_path, packwiz_path, mods_path)
        enforce_release_channel_policy(previous_snapshot, log_prefix="[Migration]")

    disabled_mods = []
    if disable_outdated_mods:
        configured_compat_loader = str(mod_loader or "").strip().lower()
        if configured_compat_loader == "fabric" and resolved_target_loader != "fabric":
            compatibility_loader = resolved_target_loader
        elif (
            configured_compat_loader
            and configured_compat_loader == current_loader
            and resolved_target_loader != current_loader
        ):
            compatibility_loader = resolved_target_loader
        elif is_supported_mod_loader(configured_compat_loader):
            compatibility_loader = normalize_mod_loader_name(
                configured_compat_loader,
                default=resolved_target_loader,
            )
        else:
            compatibility_loader = resolved_target_loader
        disabled_mods = disable_incompatible_mods(target_minecraft_version, compatibility_loader)
        print("[Migration] Running refresh...", flush=True)
        pack_manager.refresh_index(packwiz_exe_path, packwiz_path)

    print(
        f"[Migration] Minecraft {current_minecraft} -> {target_minecraft_version}"
        + (
            f", {get_mod_loader_label(current_loader)} -> {get_mod_loader_label(resolved_target_loader)} "
            f"({resolved_target_loader_version})"
            if resolved_target_loader_version
            else ""
        )
    )
    print(f"[Migration] Disabled {len(disabled_mods)} incompatible mods.")
    if disabled_mods:
        print("[Migration] Disabled mods: " + ", ".join(disabled_mods))

    return (
        target_minecraft_version,
        resolved_target_loader,
        resolved_target_loader_version,
    )


def set_bcc_config_version(bcc_path, new_pack_version):
    """Write ``modpackVersion`` into a BCC config file if it exists."""
    if not os.path.isfile(bcc_path):
        return
    try:
        with open(bcc_path, "r", encoding="utf8") as f:
            bcc_json = json.load(f)
        bcc_json["modpackVersion"] = new_pack_version
        with open(bcc_path, "w", encoding="utf8") as f:
            json.dump(bcc_json, f)
    except Exception as ex:
        print(f"[Version] Failed updating {bcc_path}: {ex}")


def bump_modpack_version(new_pack_version):
    """Update the modpack version in pack.toml and BCC configs, then create a changelog template.

    Args:
        new_pack_version: The new version string to apply.
    """
    global pack_version, changelog_factory
    if not new_pack_version:
        print("[Version] No target version provided. Skipping.")
        return

    os.chdir(packwiz_path)
    with open(packwiz_manifest, "r", encoding="utf8") as f:
        local_pack_toml = toml.load(f)

    old_pack_version = str(local_pack_toml.get("version", ""))
    local_pack_toml["version"] = new_pack_version
    with open(packwiz_manifest, "w", encoding="utf8") as f:
        toml.dump(local_pack_toml, f)

    for bcc_path in (bcc_client_config_path, bcc_server_config_path):
        set_bcc_config_version(bcc_path, new_pack_version)

    pack_version = new_pack_version
    ensure_changelog_yml(pack_version, minecraft_version, active_mod_loader, mod_loader_version)
    changelog_factory = ChangelogFactory(changelog_dir_path, modpack_name, pack_version, settings, yaml)
    print(f"[Version] Modpack version bumped: {old_pack_version} -> {new_pack_version}")


def apply_loader_metadata_to_changelog(changelog_yml, target_mod_loader, target_mod_loader_version):
    """Write mod loader name and version into a changelog YAML mapping in-place.

    Args:
        changelog_yml: A ruamel CommentedMap (or dict) representing the changelog.
        target_mod_loader: Loader name (e.g. "fabric", "neoforge").
        target_mod_loader_version: Loader version string.

    Returns:
        True if any value was changed, False if the mapping was already up-to-date.
    """
    changed = False
    normalized_loader = normalize_mod_loader_name(target_mod_loader)
    loader_label = get_mod_loader_label(normalized_loader)
    loader_version = str(target_mod_loader_version or "").strip()

    if changelog_yml.get("Mod loader") != loader_label:
        changelog_yml["Mod loader"] = loader_label
        changed = True
    if changelog_yml.get("Mod loader version") != loader_version:
        changelog_yml["Mod loader version"] = loader_version
        changed = True

    # Keep legacy key in sync for Fabric-based packs.
    if normalized_loader == "fabric" and changelog_yml.get("Fabric version") != loader_version:
        changelog_yml["Fabric version"] = loader_version
        changed = True

    return changed


def ensure_changelog_yml(target_pack_version, target_minecraft_version, target_mod_loader, target_mod_loader_version):
    """Create a changelog YAML template for the given version if one does not already exist.

    If the file exists its mod loader metadata is updated in-place.

    Args:
        target_pack_version: Modpack version string (used in the filename).
        target_minecraft_version: Minecraft version string (used in the filename).
        target_mod_loader: Loader name written into the template.
        target_mod_loader_version: Loader version written into the template.

    Returns:
        Absolute path to the changelog YAML file.
    """
    os.makedirs(changelog_dir_path, exist_ok=True)
    changelog_path = os.path.join(changelog_dir_path, changelog_filename(target_pack_version, target_minecraft_version))
    loader_label = get_mod_loader_label(target_mod_loader)
    loader_version = str(target_mod_loader_version or "").strip()
    normalized_loader = normalize_mod_loader_name(target_mod_loader)

    if not os.path.isfile(changelog_path):
        if settings.changelog_template_use_overview_layout:
            legacy_fabric_line = f"Fabric version: {loader_version}\n" if normalized_loader == "fabric" else ""
            changelog_template = (
                f"Mod loader: {loader_label}\n"
                f"Mod loader version: {loader_version}\n"
                + legacy_fabric_line
                + "Update overview:\n"
                + "Config Changes: |\n"
            )
            with open(changelog_path, "w", encoding="utf-8") as f:
                f.write(changelog_template)
        else:
            data = CommentedMap()
            data["Mod loader"] = loader_label
            data["Mod loader version"] = loader_version
            if normalized_loader == "fabric":
                data["Fabric version"] = loader_version
            data["Changes/Improvements"] = None
            data["Bug Fixes"] = None
            data["Config Changes"] = LiteralScalarString("- : [mod], [Client]")
            with open(changelog_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f)
        print(f"[Version] Created changelog template: {changelog_path}")
        return changelog_path

    with open(changelog_path, "r", encoding="utf-8") as f:
        changelog_yml = yaml.load(f) or {}
    if apply_loader_metadata_to_changelog(changelog_yml, target_mod_loader, target_mod_loader_version):
        with open(changelog_path, "w", encoding="utf-8") as f:
            yaml.dump(changelog_yml, f)
        print(f"[Version] Updated mod loader metadata in changelog: {changelog_path}")
    return changelog_path

def download_missing_comparison_files():
    """Download Packwiz snapshot data from GitHub for any changelogs that lack local comparison files."""
    if not settings.download_comparison_files:
        return

    if settings.github_auth:
        github_token = resolve_github_token(tool_cfg) or input("Your personal access token: ")
    else:
        github_token = None

    async def download_compare_files_async(input_version, destination, tag_mc_ver):
        print(f"Downloading {input_version} comparison files.")
        packwiz_root = resolve_comparison_packwiz_root(input_version, tag_mc_ver)

        local_downloader = AsyncGitHubDownloader(
            settings.repo_owner,
            settings.repo_name,
            token=github_token,
            branch=input_version,
        )
        await local_downloader.download_repo_snapshot(
            destination=destination,
            folder_mappings={
                f"{packwiz_root}/mods": "mods",
                f"{packwiz_root}/resourcepacks": "resourcepacks",
                f"{packwiz_root}/shaderpacks": "shaderpacks",
                f"{packwiz_root}/config": "config",
            },
        )

    for changelog in reversed(os.listdir(changelog_dir_path)):
        if changelog.endswith((".yml", ".yaml")):
            version, tag_mc_ver = changelog_factory.parse_changelog_filename(changelog)
            if not version or not tag_mc_ver:
                print(f"[Changelog] Skipping invalid changelog filename format: {changelog}")
                continue
            version_path = os.path.join(tempgit_path, changelog_stem(version, tag_mc_ver))
            missing_compare_data = (
                not os.path.isdir(os.path.join(version_path, "mods"))
                or not os.path.isdir(os.path.join(version_path, "resourcepacks"))
                or not os.path.isdir(os.path.join(version_path, "shaderpacks"))
                or not os.path.isdir(os.path.join(version_path, "config"))
            )
            is_current_release = str(version) == str(pack_version) and str(tag_mc_ver) == str(minecraft_version)
            if not is_current_release and (not os.path.exists(version_path) or missing_compare_data):
                os.makedirs(version_path, exist_ok=True)
                try:
                    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
                    asyncio.run(download_compare_files_async(version, version_path, tag_mc_ver))
                except Exception as ex:
                    print(ex)


def write_release_data(diff_payload=None):
    """Write the frozen, presentation-free data record for the current release.

    Merges the authored changelog YAML with the computed mod/resourcepack/
    shaderpack diffs into ``Changelogs/<version>+<mc>.json``. The release date
    is stamped once and preserved on later runs so a published record stays
    immutable while its diffs can still be regenerated during authoring.

    Args:
        diff_payload: A pack diff already computed this run (by
            ``run_changelog_auto_generation``); computed here when ``None``.
    """
    os.chdir(git_path)
    changelog_path = ensure_changelog_yml(pack_version, minecraft_version, active_mod_loader, mod_loader_version)
    with open(changelog_path, "r", encoding="utf-8") as f:
        changelog_yml = yaml.load(f) or {}

    if diff_payload is None:
        diff_payload = changelog_factory.get_current_pack_diff_payload(
            target_version=pack_version,
            mc_version=minecraft_version,
            tempgit_path=tempgit_path,
            packwiz_path=packwiz_path,
            migration_mode=bool(settings.migrate_minecraft_version),
        )

    # Published, presentation-free data lives in its own subfolder so the wiki
    # sync is a clean directory copy separate from the authored YAML.
    data_dir = os.path.join(changelog_dir_path, "data")
    os.makedirs(data_dir, exist_ok=True)
    data_path = os.path.join(data_dir, changelog_filename(pack_version, minecraft_version, ext="json"))
    released = date.today().isoformat()
    if os.path.isfile(data_path):
        try:
            with open(data_path, "r", encoding="utf-8") as f:
                released = json.load(f).get("released") or released
        except (OSError, ValueError):
            pass

    record = changelog_factory.build_release_data(
        changelog_yml, diff_payload, pack_version, minecraft_version,
        get_mod_loader_label(active_mod_loader), mod_loader_version, released,
    )
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    print(f"[Changelog] Wrote release data: {data_path}")


def run_changelog_auto_generation():
    """Run the configured automatic changelog generation steps (update overview and/or config changes).

    Returns:
        The pack diff payload computed for this release, so callers can reuse it
        instead of recomputing the (expensive) diff.
    """
    os.chdir(git_path)
    changelog_path = ensure_changelog_yml(pack_version, minecraft_version, active_mod_loader, mod_loader_version)

    diff_payload = changelog_factory.get_current_pack_diff_payload(
        target_version=pack_version,
        mc_version=minecraft_version,
        tempgit_path=tempgit_path,
        packwiz_path=packwiz_path,
        migration_mode=bool(settings.migrate_minecraft_version),
    )
    active_mod_names = parse_active_projects(mods_path, "name")
    initialize_modlist_label_index(active_mod_names)
    packwiz_config_path = os.path.join(packwiz_path, "config")

    if settings.auto_generate_update_overview or settings.generate_update_summary_only:
        maybe_generate_update_overview(changelog_path, diff_payload, settings, yaml)
    if settings.auto_generate_config_changes or settings.generate_update_summary_only:
        maybe_generate_config_changes(changelog_path, diff_payload, settings, yaml, packwiz_config_path)
    return diff_payload


def clear_stored_repository_data():
    """Delete and recreate the tempgit and prev_release directories to wipe cached repository data."""
    repo_data_paths = [tempgit_path, prev_release]
    for path in repo_data_paths:
        if os.path.isdir(path):
            rmtree(path)
        os.makedirs(path, exist_ok=True)
        print(f"[RepoData] Cleared: {path}")

############################################################
# Project activation

def load_curseforge_key():
    """Apply the user's CurseForge API key (tool config or cf-api-key.txt).

    When neither source has a key, the packwiz community default stays in effect.
    """
    key = resolve_curseforge_key(tool_cfg)
    if key:
        configure_curseforge_api_key(key)


def activate_project(project_root):
    """Bind all project-scoped globals to the given modpack project root.

    Runs preflight (dirs, settings.yml, gitignore, legacy cache migration),
    reads pack.toml, loads settings, rebuilds the changelog factory, and
    leaves the working directory at the project's Packwiz folder — the
    resting state the workflow functions assume. Persists the project as
    last-used on success. On failure the previously active project (if any)
    remains fully intact.

    Returns:
        True when activation succeeded, False otherwise.
    """
    global git_path, packwiz_path, serverpack_path, packwiz_exe_path
    global bcc_client_config_path, bcc_server_config_path
    global export_path, tempfolder_path, temp_mods_path, settings_path
    global prev_release, changelog_dir_path, tempgit_path
    global mods_path, crash_assistant_config_path, crash_assistant_markdown_path
    global settings, changelog_factory
    global pack_version, modpack_name, minecraft_version, active_mod_loader, mod_loader_version

    problems = validate_project_root(project_root)
    if problems:
        for problem in problems:
            print(f"[Project] {problem}")
        return False

    paths = compute_project_paths(project_root)
    try:
        preflight_project(paths, os.path.join(tool_dir, "settings_template.yml"))
        pack_toml = read_pack_manifest(paths)
    except RuntimeError as ex:
        print(f"[Project] {ex}")
        return False

    git_path = paths.root
    packwiz_path = paths.packwiz_path
    serverpack_path = paths.serverpack_path
    bcc_client_config_path = paths.bcc_client_config_path
    bcc_server_config_path = paths.bcc_server_config_path
    export_path = paths.export_path
    tempfolder_path = paths.tempfolder_path
    temp_mods_path = paths.temp_mods_path
    settings_path = paths.settings_path
    mods_path = paths.mods_path
    prev_release = paths.prev_release_path
    changelog_dir_path = paths.changelog_dir_path
    tempgit_path = paths.tempgit_path
    crash_assistant_config_path = paths.crash_assistant_config_path
    crash_assistant_markdown_path = paths.crash_assistant_markdown_path

    packwiz_exe_path = resolve_packwiz_exe(tool_cfg)

    pack_version = pack_toml["version"]
    modpack_name = pack_toml["name"]
    minecraft_version = pack_toml["versions"]["minecraft"]
    active_mod_loader, mod_loader_version = get_pack_mod_loader_details(pack_toml)

    settings = load_settings(settings_path, yaml)
    changelog_factory = ChangelogFactory(changelog_dir_path, modpack_name, pack_version, settings, yaml)

    os.chdir(packwiz_path)

    print(f"""
Project: {git_path}
Modpack: {modpack_name}
Version: {pack_version}
Minecraft: {minecraft_version}
Modloader: {get_mod_loader_label(active_mod_loader)} {mod_loader_version}""")

    if settings.print_path_debug:
        print("[DEBUG] " + git_path)
        print("[DEBUG] " + packwiz_path)
        print("[DEBUG] " + packwiz_exe_path)
        print("[DEBUG] " + bcc_client_config_path)
        print("[DEBUG] " + bcc_server_config_path)

    registry_changed = (tool_cfg.last_used_project != git_path
                        or find_project(tool_cfg, git_path) is None)
    register_project(tool_cfg, git_path)
    tool_cfg.last_used_project = git_path
    if registry_changed:
        save_tool_config(tool_cfg, yaml)
    return True


def select_project_interactive():
    """Project picker: choose, add, or remove registered modpack projects.

    Returns:
        The chosen project root, or None if the user chose to exit.
    """
    def prompt_for_new_project():
        while True:
            raw = input("Path to the modpack project root (drag & drop works, Enter to cancel): ")
            root = normalize_drag_drop_path(raw)
            if not root:
                return None
            problems = validate_project_root(root)
            if not problems:
                register_project(tool_cfg, root)
                save_tool_config(tool_cfg, yaml)
                return root
            for problem in problems:
                print(f"[Project] {problem}")

    # First run from an old embedded install: offer the surrounding repo.
    if not tool_cfg.projects:
        legacy_parent = os.path.dirname(tool_dir)
        if (os.path.basename(tool_dir) == LEGACY_TOOL_DIR_NAME
                and not validate_project_root(legacy_parent)):
            if prompt_yes(f"Detected modpack project at '{legacy_parent}'. Register it? [Y]: "):
                register_project(tool_cfg, legacy_parent)
                save_tool_config(tool_cfg, yaml)
                return legacy_parent
        print("No modpack projects registered yet.")
        return prompt_for_new_project()

    while True:
        print("\nRegistered modpack projects:")
        for index, project in enumerate(tool_cfg.projects, start=1):
            print(f"{index}) {project.name}  ({project.root})")
        print("A) Add project")
        print("R) Remove project")
        print("0) Exit")
        choice = input("Selection [1]: ").strip() or "1"

        if choice == "0":
            return None
        if choice.lower() == "a":
            root = prompt_for_new_project()
            if root:
                return root
            continue
        if choice.lower() == "r":
            index_raw = input("Number of the project to remove: ").strip()
            if index_raw.isdigit() and 1 <= int(index_raw) <= len(tool_cfg.projects):
                removed = tool_cfg.projects[int(index_raw) - 1]
                remove_project(tool_cfg, removed)
                save_tool_config(tool_cfg, yaml)
                print(f"[Project] Removed '{removed.name}' from the registry (files untouched).")
            else:
                print("Invalid selection.")
            if not tool_cfg.projects:
                return prompt_for_new_project()
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(tool_cfg.projects):
            return tool_cfg.projects[int(choice) - 1].root
        print(f"Unknown choice '{choice}'.")

############################################################
# Main Program

def has_special_menu_action_selected(settings):
    """Return True if any single-action shortcut flag is enabled in settings."""
    return any(
        [
            settings.refresh_only,
            settings.update_mods_only,
            settings.bump_version_only,
            settings.clear_repo_data_only,
            settings.generate_update_summary_only,
            settings.list_disabled_mods_only,
            settings.add_mod_only,
            settings.find_orphaned_libraries_only,
        ]
    )


def run_special_menu_action(settings):
    """Execute the single special action selected through the menu (e.g. update mods, bump version)."""
    if settings.clear_repo_data_only:
        clear_stored_repository_data()
    elif settings.bump_version_only:
        bump_modpack_version(settings.bump_target_version)
        pack_manager.refresh_index(packwiz_exe_path, packwiz_path)
    elif settings.generate_update_summary_only:
        download_missing_comparison_files()
        run_changelog_auto_generation()
    elif settings.list_disabled_mods_only:
        list_disabled_mods()
    elif settings.add_mod_only:
        add_mod_via_prompt()
    elif settings.find_orphaned_libraries_only:
        find_and_remove_orphaned_library_mods()
    elif settings.update_mods_only:
        previous_snapshot = snapshot_mod_toml_content()
        pack_manager.refresh_index(packwiz_exe_path, packwiz_path)

        pinned_updates = find_pinned_mods_with_available_updates()
        temp_unpinned_mod_files = []
        allowed_alpha_mod_files = []
        if pinned_updates:
            selected_pinned_mod_files, allowed_alpha_mod_files = prompt_for_pinned_mod_updates(pinned_updates)
            if selected_pinned_mod_files:
                temp_unpinned_mod_files = set_pin_state_for_mod_files(selected_pinned_mod_files, should_pin=False)
                if temp_unpinned_mod_files:
                    pack_manager.refresh_index(packwiz_exe_path, packwiz_path)

        try:
            pack_manager.update_all_mods(packwiz_exe_path, packwiz_path, mods_path)
        finally:
            if temp_unpinned_mod_files:
                repinned_mod_files = set_pin_state_for_mod_files(temp_unpinned_mod_files, should_pin=True)
                if repinned_mod_files:
                    pack_manager.refresh_index(packwiz_exe_path, packwiz_path)
                    print(f"[PackWiz] Re-pinned {len(repinned_mod_files)} pinned mods after update.")

        enforce_release_channel_policy(
            previous_snapshot,
            log_prefix="[Update]",
            allowed_alpha_mod_files=allowed_alpha_mod_files,
        )
        pack_manager.refresh_index(packwiz_exe_path, packwiz_path)
        print("[PackWiz] Mods updated.")

        updated_disabled_mods = find_updated_disabled_mods(previous_snapshot)
        if updated_disabled_mods:
            updated_disabled_names = [mod_name for _, mod_name in updated_disabled_mods]
            print("[PackWiz] Updated mods that are still disabled: " + ", ".join(updated_disabled_names))
            if input("Enable these updated disabled mods? [N]: ") in ("y", "Y", "yes", "Yes"):
                enabled_mods = enable_mods_by_files([mod_file for mod_file, _ in updated_disabled_mods])
                pack_manager.refresh_index(packwiz_exe_path, packwiz_path)
                print(f"[PackWiz] Re-enabled {len(enabled_mods)} updated disabled mods.")
    elif settings.refresh_only:
        pack_manager.refresh_index(packwiz_exe_path, packwiz_path)


def run_release_notes_generation(settings):
    """Generate CurseForge and Modrinth release-note Markdown files from the current changelog YAML."""
    os.chdir(git_path)
    major_minecraft_version = '.'.join(minecraft_version.split('.', 2)[:2])
    md_element_full_changelog = f"**[[Full Changelog]](https://crismpack.net/{modpack_name.lower().split(' ', 1)[0]}/changelogs/{major_minecraft_version}/{minecraft_version}#{format_version_anchor(pack_version)})**"
    md_element_pre_release = '**This is a pre-release. Here be dragons!**'
    md_element_bh_banner = f"[![BisectHosting Banner]({settings.bh_banner})](https://bisecthosting.com/CRISM)"
    mdFile_CF = MdUtils(file_name='CurseForge-Release')
    mdFile_MR = MdUtils(file_name='Modrinth-Release')

    if "beta" in pack_version or "alpha" in pack_version:
        print("pack_version = " + pack_version)
        mdFile_CF.new_paragraph(md_element_pre_release)
        mdFile_MR.new_paragraph(md_element_pre_release)

    # Creates the template when missing and syncs loader metadata otherwise.
    changelog_path = ensure_changelog_yml(pack_version, minecraft_version, active_mod_loader, mod_loader_version)

    with open(changelog_path, "r", encoding="utf-8") as f:
        changelog_yml = yaml.load(f) or {}

    try:
        update_overview = changelog_yml['Update overview']
        mdFile_CF.new_paragraph(markdown.markdown_list_maker(update_overview))
        mdFile_MR.new_paragraph(markdown.markdown_list_maker(update_overview))
    except Exception:
        try:
            improvements = changelog_yml.get('Changes/Improvements')
            bug_fixes = changelog_yml.get('Bug Fixes')

            if improvements:
                mdFile_CF.new_paragraph("### Changes/Improvements ⭐")
                mdFile_CF.new_paragraph(markdown.markdown_list_maker(improvements))
                mdFile_MR.new_paragraph(markdown.markdown_list_maker(improvements))
            if bug_fixes:
                mdFile_CF.new_paragraph("### Bug Fixes 🪲")
                mdFile_CF.new_paragraph(markdown.markdown_list_maker(bug_fixes))
                mdFile_MR.new_paragraph(markdown.markdown_list_maker(bug_fixes))
        except Exception:
            print(f"No 'Update overview' or 'Changes/Improvements' found for {pack_version}...")

    mdFile_CF.new_paragraph("#### " + md_element_full_changelog)
    mdFile_CF.new_paragraph("<br>")
    mdFile_CF.new_paragraph(md_element_bh_banner)
    mdFile_CF.create_md_file()
    mdFile_MR.new_paragraph(md_element_full_changelog)
    mdFile_MR.create_md_file()


def update_publish_workflow(settings):
    """Update MC_VERSION, RELEASE_TYPE, and PRE_RELEASE fields in the GitHub publish workflow YAML."""
    os.chdir(git_path)
    publish_workflow_path = os.path.join(git_path, ".github", "workflows", "publish.yml")

    with open(publish_workflow_path, "r", encoding="utf-8") as pw_file:
        publish_workflow_yml = yaml.load(pw_file) or {}

    publish_workflow_yml['env']['MC_VERSION'] = minecraft_version

    if "beta" in pack_version:
        pw_release_type = "beta"; pw_prerelease = True
    elif "alpha" in pack_version:
        pw_release_type = "alpha"; pw_prerelease = True
    else:
        pw_release_type = "release"; pw_prerelease = False

    publish_workflow_yml['env']['RELEASE_TYPE'] = pw_release_type
    publish_workflow_yml['env']['PRE_RELEASE'] = pw_prerelease

    with open(publish_workflow_path, "w", encoding="utf-8") as pw_file:
        yaml.dump(publish_workflow_yml, pw_file)


def update_bcc_versions(settings):
    """Write the current pack_version into the BCC client and/or server config JSON files."""
    if settings.export_client:
        set_bcc_config_version(bcc_client_config_path, pack_version)
    if settings.export_server:
        set_bcc_config_version(bcc_server_config_path, pack_version)


def update_crash_assistant_modlist(settings):
    """Regenerate the Crash Assistant modlist JSON and the modlist.md Markdown file."""
    mod_filenames_json = parse_filenames_as_json(mods_path)
    with open(crash_assistant_config_path, "w", encoding="utf8") as output_file:
        output_file.write(mod_filenames_json)
    combined_modlist_markdown = build_combined_modlist_markdown(
        mods_path,
        include_side_tags=settings.modlist_side_tag
    )
    with open(crash_assistant_markdown_path, "w", encoding="utf8") as output_file:
        output_file.write(combined_modlist_markdown)


def _run_multi_platform_client_export():
    """Export the client pack to CurseForge and Modrinth formats natively."""
    # Generate CurseForge export
    print("[Export] Generating CurseForge pack...")
    cf_zip_path = pack_manager.export_cf_pack(packwiz_path, mods_path, packwiz_path, side="client")
    move(cf_zip_path, os.path.join(export_path, os.path.basename(cf_zip_path)))
    print("[Export] CurseForge exported.")

    # Generate Modrinth export
    print("[Export] Generating Modrinth pack...")
    mr_zip_path = pack_manager.export_mrpack(packwiz_path, mods_path, packwiz_path, side="client")
    move(mr_zip_path, os.path.join(export_path, os.path.basename(mr_zip_path)))
    print("[Export] Modrinth exported.")


def _run_server_export():
    """Export the server pack and assemble the server folder archive."""
    server_zip_path = pack_manager.run_packwiz_export(packwiz_exe_path, packwiz_path, "curseforge")
    server_zip_name = os.path.basename(server_zip_path)
    move(server_zip_path, os.path.join(export_path, server_zip_name))
    print(f"[PackWiz] Server exported as {server_zip_name}.")

    os.chdir(git_path)
    if os.path.isdir(tempfolder_path):
        rmtree(tempfolder_path)

    copytree("Server Pack", tempfolder_path)

    server_mods_input = input(
        f"Create a new modpack instance in the CurseForge launcher using the {server_zip_name} file. "
        "Then drag the mods folder from that instance into the terminal: "
    )
    server_mods_path = normalize_drag_drop_path(server_mods_input)
    if not server_mods_path:
        raise ValueError("No source mods directory was provided.")
    if not os.path.isdir(server_mods_path):
        raise FileNotFoundError(
            f"Source mods directory was not found or is invalid: {server_mods_path!r}"
        )

    copytree(server_mods_path, temp_mods_path, dirs_exist_ok=True)

    os.chdir(temp_mods_path)
    for file in os.listdir():
        if file in settings.server_mods_remove_list:
            os.remove(file)

    os.chdir(export_path)
    make_archive(f"{modpack_name}-Server-{pack_version}", 'zip', tempfolder_path)


def main():
    """Run the full export workflow or the selected special action for one loop iteration."""
    global minecraft_version, active_mod_loader, mod_loader_version

    special_menu_action_selected = has_special_menu_action_selected(settings)

    if not special_menu_action_selected:
        if settings.show_export_mode_notice and (settings.export_client or settings.export_server):
            input("Using modular export compatibility settings. Press Enter to continue...")

        if settings.migrate_minecraft_version:
            minecraft_version, active_mod_loader, mod_loader_version = migrate_minecraft_version(
                target_minecraft_version=settings.migration_target_minecraft,
                target_fabric_version=settings.migration_target_fabric,
                target_mod_loader=settings.migration_target_mod_loader,
                target_mod_loader_version=settings.migration_target_mod_loader_version,
                update_all_mods=settings.migration_update_all_mods,
                disable_outdated_mods=settings.migration_disable_incompatible_mods,
                mod_loader=settings.migration_mod_loader,
            )
            # Bump after the MC globals update so the changelog template pairs
            # the new pack version with the new Minecraft version.
            if settings.bump_target_version:
                bump_modpack_version(settings.bump_target_version)

        #----------------------------------------
        # Download comparison files.
        #----------------------------------------
        download_missing_comparison_files()

        #----------------------------------------
        # Auto-generate changelog update overview.
        #----------------------------------------
        auto_gen_diff_payload = None
        if settings.auto_generate_update_overview or settings.auto_generate_config_changes:
            auto_gen_diff_payload = run_changelog_auto_generation()

        #----------------------------------------
        # Write the frozen data-first release record (reusing the diff already
        # computed by auto-generation when it ran).
        #----------------------------------------
        if settings.generate_primary_changelog:
            write_release_data(auto_gen_diff_payload)

        #----------------------------------------
        # Generate CHANGELOG.md file (legacy renderer; retired once the wiki
        # data renderer is live).
        #----------------------------------------
        if settings.generate_primary_changelog:
            os.chdir(git_path)
            changelog_factory.build_markdown_changelog(
                settings.repo_owner, settings.repo_name, tempgit_path,
                packwiz_path, repo_branch=settings.repo_main_branch, mc_version=minecraft_version
            )

        #----------------------------------------
        # Update publish workflow values.
        #----------------------------------------
        if settings.update_publish_workflow:
            update_publish_workflow(settings)

        #----------------------------------------
        # Create release notes.
        #----------------------------------------
        if settings.create_release_notes:
            run_release_notes_generation(settings)


        #----------------------------------------
        # Update BCC version number.
        #----------------------------------------
        if settings.update_bcc_version:
            update_bcc_versions(settings)

        #----------------------------------------
        # Update 'Crash Assistant' modlist.
        #----------------------------------------
        if settings.update_crash_assistant_modlist:
            update_crash_assistant_modlist(settings)

        #----------------------------------------
        # Export client pack. (CurseForge with pack_manager)
        #----------------------------------------
        os.chdir(packwiz_path)
        pack_manager.refresh_index(packwiz_exe_path, packwiz_path)

        if settings.export_client and not settings.client_export_multi_platform:
            fmt = str(settings.client_export_format or "curseforge").strip().lower()
            label = "Modrinth" if fmt == "modrinth" else "CurseForge"
            print(f"[Export] Generating {label} pack (packwiz)...")
            client_zip_path = pack_manager.run_packwiz_export(packwiz_exe_path, packwiz_path, fmt)
            move(client_zip_path, os.path.join(export_path, os.path.basename(client_zip_path)))
            print(f"[Export] {label} exported.")

        #----------------------------------------
        # Export client pack. (CurseForge & Modrinth — native fingerprint export)
        #----------------------------------------
        if settings.export_client and settings.client_export_multi_platform:
            _run_multi_platform_client_export()

        #----------------------------------------
        # Export server pack
        # ----------------------------------------
        if settings.export_server:
            _run_server_export()

        #----------------------------------------
        # Temp cleanup
        #----------------------------------------
        if settings.cleanup_temp and os.path.isdir(tempfolder_path):
            rmtree(tempfolder_path)
            print("Temp folder cleanup finished.")

        os.chdir(packwiz_path)
        pack_manager.refresh_index(packwiz_exe_path, packwiz_path)

    else:
        run_special_menu_action(settings)


def select_and_activate_project():
    """Run the picker until a project activates.

    Returns:
        True once a project is active, False if the user chose to exit.
    """
    while True:
        root = select_project_interactive()
        if root is None:
            return False
        if activate_project(root):
            return True


if __name__ == "__main__":
    try:
        print(launch_message)
        tool_cfg = load_tool_config(yaml)
        load_curseforge_key()

        # Activate the last-used project, falling back to the picker.
        last_used = tool_cfg.last_used_project
        if not (last_used and activate_project(last_used)) and not select_and_activate_project():
            print("No project selected. Exiting.")
            exit(0)

        while True:
            menu_result = configure_actions_via_menu(settings)
            if menu_result is False:
                print("No action selected. Exiting.")
                break
            if menu_result == "switch_project":
                # Cancelling the picker keeps the current project active.
                select_and_activate_project()
                continue
            print("")
            try:
                main()
                input("\nWorkflow complete. Press Enter to return to the menu...")
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f"\n[ERROR] Workflow crashed: {e}")
                input("Press Enter to return to the menu...")
    except KeyboardInterrupt:
        print("Operation aborted by user.")
        exit(-1)
