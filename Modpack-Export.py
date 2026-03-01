launch_message = """
▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
█                           █
█  HaXr's Modpack CLI Tool  █
█                           █
▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀"""

import os, sys
import os.path
import json
import re
import subprocess
from shutil import rmtree, make_archive, move, copytree
from pathlib import Path

import toml  # pip install toml
# import yaml  # REMOVE PyYAML

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import LiteralScalarString

from mdutils.mdutils import MdUtils
import requests

# Settings
from dataclasses import dataclass
from typing import List, Optional

# GitHub Download
from GitHubDownloader import AsyncGitHubDownloader
import asyncio

# Changelog stuff
from ChangelogFactory import ChangelogFactory

# Markdown Stuff
import MarkdownHelper as markdown

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

user_path = os.path.expanduser("~")

# Get the directory containing the script
script_path = os.path.abspath(__file__)  # Absolute path to the script
git_path = str(os.path.dirname(os.path.dirname(script_path)))

os.chdir(git_path)

packwiz_path = os.path.join(git_path, "Packwiz")
serverpack_path = os.path.join(git_path, "Server Pack")
packwiz_exe_path = os.path.join(user_path, "go", "bin", "packwiz.exe")
packwiz_manifest = "pack.toml"
bcc_client_config_path = os.path.join(packwiz_path, "config", "bcc.json")
bcc_server_config_path = os.path.join(serverpack_path, "config", "bcc.json")
export_path = os.path.join(git_path, "Export")
tempfolder_path = os.path.join(export_path, "temp")
temp_mods_path = os.path.join(tempfolder_path, "mods")
settings_path = os.path.join(git_path, "settings.yml")
packwiz_mods_path = os.path.join(packwiz_path, "mods")
prev_release = os.path.join(git_path, "Modpack-CLI-Tool", "prev_release")
changelog_dir_path = os.path.join(git_path, "Changelogs")
tempgit_path = os.path.join(git_path, "Modpack-CLI-Tool", "tempgit")
mods_path = os.path.join(packwiz_path, "mods")
crash_assistant_config_path = os.path.join(packwiz_path, "config", "crash_assistant", "modlist.json")
crash_assistant_markdown_path = os.path.join(git_path, "modlist.md")
modrinth_api_base = "https://api.modrinth.com/v2"
curseforge_api_base = "https://www.curseforge.com/api/v1"
_mod_label_index_cache = None
SUPPORTED_MOD_LOADERS = ("fabric", "quilt", "forge", "neoforge")
MOD_LOADER_LABELS = {
    "fabric": "Fabric",
    "quilt": "Quilt",
    "forge": "Forge",
    "neoforge": "NeoForge",
}

############################################################
# Functions

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

def determine_server_export():
    """Determine whether the server pack should be exported or not and return a boolean."""
    if settings.export_server:
        if input("Want to export server pack? [N]: ") in ("y", "Y", "yes", "Yes"):
            return True
        else:
            return False
    else:
        return False


def normalize_drag_drop_path(raw_path: str) -> str:
    """Normalize terminal drag-and-drop paths (often wrapped in quotes)."""
    cleaned_path = str(raw_path or "").strip()
    if len(cleaned_path) >= 2 and cleaned_path[0] == cleaned_path[-1] and cleaned_path[0] in ("'", '"'):
        cleaned_path = cleaned_path[1:-1].strip()
    cleaned_path = os.path.expanduser(os.path.expandvars(cleaned_path))
    return os.path.normpath(cleaned_path) if cleaned_path else ""


def ensure_migration_targets(settings):
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


def uses_llm_config_changes(settings) -> bool:
    return str(settings.auto_config_provider).strip().lower() == "ollama"


def get_config_changes_mode_label(settings) -> str:
    if uses_llm_config_changes(settings):
        model = str(settings.auto_config_model).strip() or "default-model"
        return f"LLM ({model})"
    provider = str(settings.auto_config_provider).strip() or "unknown"
    return f"Fallback (deterministic, provider '{provider}' unsupported)"


def configure_actions_via_menu(settings):
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

    config_mode_label = get_config_changes_mode_label(settings)

    print(
        f"""
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
0) Exit

Config Changes generator: {config_mode_label}
"""
    )

    choice = input("Selection [1]: ").strip() or "1"

    if choice == "0":
        return False

    # Reset runtime flow toggles before applying chosen mode.
    settings.refresh_only = False
    settings.update_mods_only = False
    settings.bump_version_only = False
    settings.clear_repo_data_only = False
    settings.generate_update_summary_only = False
    settings.list_disabled_mods_only = False
    settings.add_mod_only = False
    settings.migrate_minecraft_version = False
    settings.export_client = False
    settings.export_server = False

    if choice == "1":
        # Keep the configured export_client value while preserving existing server prompt behavior.
        with open(settings_path, "r", encoding="utf-8") as s_file:
            settings_yml_local = yaml.load(s_file) or {}
        settings.export_client = bool(settings_yml_local.get("export_client", False))
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
        target_version = input(f"New modpack version [{pack_version}]: ").strip()
        settings.bump_target_version = target_version if target_version else pack_version
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

    print(f"Unknown choice '{choice}'. Falling back to configured workflow.")
    with open(settings_path, "r", encoding="utf-8") as s_file:
        settings_yml_local = yaml.load(s_file) or {}
    settings.export_client = bool(settings_yml_local.get("export_client", False))
    settings.export_server = determine_server_export()
    prompt_changelog_autogen_overwrite()
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


def add_mod_via_prompt():
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

    os.chdir(packwiz_path)
    cmd = f'{packwiz_exe_path} {source} add "{add_value}"'
    result = subprocess.call(cmd, shell=True)
    if result != 0:
        print(f"[PackWiz] Add command failed with exit code {result}.")
        return False

    subprocess.call(f"{packwiz_exe_path} refresh", shell=True)
    print(f"[PackWiz] Added from {source}: {add_value}")
    return True


def make_and_delete_dir(dir):
    """Clear the directory if it exists, or create it."""
    if os.path.exists(dir):
        rmtree(dir)
        os.makedirs(dir)
    else:
        os.makedirs(dir)


def get_latest_release_version(owner, repo):
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    headers = {"Accept": "application/vnd.github.v3+json"}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data.get("tag_name", "No releases found.")
    except requests.exceptions.HTTPError as http_err:
        return f"HTTP error occurred: {http_err}"
    except Exception as err:
        return f"Error occurred: {err}"


def download_versioning_helper(local_version: str):
    if "alpha" in local_version or "beta" in local_version:
        return local_version.replace("-", "_")
    else:
        return local_version + "+"


def is_version_in_range(input_version, min_version=None, max_version=None, include_min=True, include_max=True):
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


def clear_mmc_cache(path):
    os.chdir(path)
    retain = ["packwiz-installer.jar"]
    for item in os.listdir(os.getcwd()):
        if item not in retain:
            try:
                os.remove(item)
            except OSError:
                pass
            try:
                rmtree(item)
            except OSError:
                pass


def normalize_disabled_side(side_value):
    side_text = str(side_value).strip()
    if "disabled" in side_text:
        return side_text
    side_base = side_text.split("(", 1)[0].strip() or "both"
    return f"{side_base}(disabled)"


def normalize_enabled_side(side_value):
    side_text = str(side_value).strip()
    if "disabled" not in side_text:
        return side_text or "both"
    side_base = side_text.split("(", 1)[0].strip() or "both"
    return side_base


def snapshot_mod_toml_content():
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
    try:
        with open(os.path.join(packwiz_path, packwiz_manifest), "r", encoding="utf8") as f:
            local_pack_toml = toml.load(f)
    except Exception as ex:
        print(f"[Update] Failed to read pack manifest for update constraints: {ex}")
        fallback_loader = normalize_mod_loader_name(globals().get("active_mod_loader", "fabric"))
        return [str(minecraft_version)], [fallback_loader]

    versions = local_pack_toml.get("versions", {})
    game_version = str(versions.get("minecraft", minecraft_version))
    loader_order = ("fabric", "quilt", "forge", "neoforge")
    loaders = [loader for loader in loader_order if versions.get(loader)]
    if not loaders:
        loaders = ["fabric"]
    return [game_version], loaders


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
        response = requests.get(f"{modrinth_api_base}/version/{version_id}", timeout=20)
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
            f"{modrinth_api_base}/project/{project_id}/version",
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


def get_alpha_update_policy():
    raw_policy = str(getattr(settings, "alpha_update_policy", "prompt")).strip().lower()
    if raw_policy in ("always_skip", "skip", "never"):
        return "always_skip"
    if raw_policy in ("always_allow", "allow"):
        return "always_allow"
    return "prompt"


def should_keep_alpha_update(mod_name, current_channel, log_prefix="[Update]"):
    policy = get_alpha_update_policy()
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


def enforce_release_channel_policy(previous_snapshot, log_prefix="[Update]", allowed_alpha_mod_files=None):
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
        if should_keep_alpha_update(mod_name, previous_channel, log_prefix=log_prefix):
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
                and get_alpha_update_policy() != "always_skip"
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
            f"{curseforge_api_base}/mods/{normalized_project_id}/files/{normalized_file_id}",
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
                f"{curseforge_api_base}/mods/{normalized_project_id}/files",
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
        if not should_keep_alpha_update(mod_name, current_channel, log_prefix="[Migration]"):
            return None
    return latest_any


def try_retarget_modrinth_mod_to_target(
    item_path,
    mod_toml,
    target_minecraft_version,
    mod_loader,
    version_cache,
    project_versions_cache,
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


def attempt_packwiz_targeted_mod_update(item, mod_toml):
    candidate_identifiers = []
    mod_name = str(mod_toml.get("name", "")).strip()
    if mod_name:
        candidate_identifiers.append(mod_name)

    item_base = re.sub(r"\.pw\.toml$", "", str(item), flags=re.IGNORECASE).strip()
    if item_base:
        candidate_identifiers.append(item_base)

    candidate_identifiers.append(f"mods/{item}")

    seen = set()
    ordered_identifiers = []
    for identifier in candidate_identifiers:
        key = str(identifier).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered_identifiers.append(str(identifier).strip())

    for identifier in ordered_identifiers:
        command = f'{packwiz_exe_path} update "{identifier}" -y'
        exit_code = subprocess.call(
            command,
            shell=True,
            cwd=packwiz_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if exit_code == 0:
            return True, identifier

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
    try:
        replaced_with_modrinth, modrinth_target = try_retarget_modrinth_mod_to_target(
            item_path=item_path,
            mod_toml=mod_toml,
            target_minecraft_version=target_minecraft_version,
            mod_loader=mod_loader,
            version_cache=version_cache,
            project_versions_cache=project_versions_cache,
        )
        if replaced_with_modrinth:
            return True, f"Modrinth {modrinth_target}"
    except Exception as ex:
        mod_name = mod_toml.get("name", item)
        print(f"[Migration] Failed Modrinth retarget for '{mod_name}': {ex}")

    updated_by_packwiz, packwiz_identifier = attempt_packwiz_targeted_mod_update(item, mod_toml)
    if not updated_by_packwiz:
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
    disabled_mods = []
    replaced_mods = []
    version_cache = {}
    project_versions_cache = {}
    curseforge_file_cache = {}
    curseforge_project_files_cache = {}
    os.chdir(mods_path)
    for item in sorted(os.listdir()):
        item_path = os.path.join(mods_path, item)
        if not os.path.isfile(item_path) or not item.endswith(".toml"):
            continue
        try:
            with open(item_path, "r", encoding="utf8") as f:
                mod_toml = toml.load(f)
            side_value = str(mod_toml.get("side", "both"))
            if "disabled" in side_value:
                continue

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
                replaced_mods.append(f"{mod_toml.get('name', item)} -> {replacement_details}")
                continue

            mod_toml["side"] = normalize_disabled_side(side_value)
            with open(item_path, "w", encoding="utf8") as f:
                toml.dump(mod_toml, f)
            disabled_mods.append(f"{mod_toml.get('name', item)} ({compatibility_source})")
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

    print("[Migration] Running packwiz refresh...", flush=True)
    subprocess.call(f"{packwiz_exe_path} refresh", shell=True)
    if update_all_mods:
        previous_snapshot = snapshot_mod_toml_content()
        print("[Migration] Running packwiz update --all -y (this can take a while)...", flush=True)
        subprocess.call(f"{packwiz_exe_path} update --all -y", shell=True)
        enforce_release_channel_policy(previous_snapshot, log_prefix="[Migration]")
        print("[Migration] Running packwiz refresh...", flush=True)
        subprocess.call(f"{packwiz_exe_path} refresh", shell=True)

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
        print("[Migration] Running packwiz refresh...", flush=True)
        subprocess.call(f"{packwiz_exe_path} refresh", shell=True)

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


def bump_modpack_version(new_pack_version):
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
        if not os.path.isfile(bcc_path):
            continue
        try:
            with open(bcc_path, "r", encoding="utf8") as f:
                bcc_json = json.load(f)
            bcc_json["modpackVersion"] = new_pack_version
            with open(bcc_path, "w", encoding="utf8") as f:
                json.dump(bcc_json, f)
        except Exception as ex:
            print(f"[Version] Failed updating {bcc_path}: {ex}")

    pack_version = new_pack_version
    ensure_changelog_yml(pack_version, minecraft_version, active_mod_loader, mod_loader_version)
    changelog_factory = ChangelogFactory(changelog_dir_path, modpack_name, pack_version, settings, yaml)
    print(f"[Version] Modpack version bumped: {old_pack_version} -> {new_pack_version}")


def apply_loader_metadata_to_changelog(changelog_yml, target_mod_loader, target_mod_loader_version):
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
    os.makedirs(changelog_dir_path, exist_ok=True)
    changelog_path = os.path.join(changelog_dir_path, f"{target_pack_version}+{target_minecraft_version}.yml")
    loader_label = get_mod_loader_label(target_mod_loader)
    loader_version = str(target_mod_loader_version or "").strip()
    normalized_loader = normalize_mod_loader_name(target_mod_loader)

    if not os.path.isfile(changelog_path):
        if settings.breakneck_fixes:
            legacy_fabric_line = f"Fabric version: {loader_version}\n" if normalized_loader == "fabric" else ""
            breakneck_template = (
                f"version: {target_pack_version}\n"
                f"mc_version: {target_minecraft_version}\n"
                "\n"
                f"Mod loader: {loader_label}\n"
                f"Mod loader version: {loader_version}\n"
                + legacy_fabric_line
                + "Update overview:\n"
                + "Config Changes: |\n"
            )
            with open(changelog_path, "w", encoding="utf-8") as f:
                f.write(breakneck_template)
        else:
            data = CommentedMap()
            data["version"] = target_pack_version
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


def _extract_modified_names(modified_items):
    return [str(item[0]) for item in modified_items if isinstance(item, (list, tuple)) and item]


def _yaml_plain_safe_name(name):
    # Prevent YAML from forcing quoted scalars for names containing ": ".
    return str(name).replace(": ", ":\u00A0")


def _format_quoted_names(names):
    cleaned = [_yaml_plain_safe_name(str(name).strip()) for name in names if str(name).strip()]
    return ", ".join(f"'{name}'" for name in cleaned)


def _format_name_list(names):
    cleaned = [_yaml_plain_safe_name(str(name).strip()) for name in names if str(name).strip()]
    quoted = [f"'{name}'" for name in cleaned]
    if not quoted:
        return ""
    if len(quoted) == 1:
        return quoted[0]
    if len(quoted) == 2:
        return f"{quoted[0]} & {quoted[1]}"
    return f"{', '.join(quoted[:-1])} & {quoted[-1]}"


def _resolve_mod_display_label(name: str) -> str:
    raw_name = str(name or "").strip()
    if not raw_name:
        return ""

    # Remove side suffixes used in some diff views, then normalize trailing separators.
    base_name = re.sub(r"\s*`[^`]+`\s*$", "", raw_name).strip()
    base_name = re.sub(r"\s*\[[^\]]+\]\s*$", "", base_name).strip()
    base_name = re.sub(r"\s*-\s*$", "", base_name).strip() or base_name

    label_index = _load_modlist_label_index()
    index = label_index.get("index", {})
    entries = label_index.get("entries", [])

    candidate_keys = [
        _normalize_lookup_key(base_name),
        _normalize_lookup_key(raw_name),
    ]
    for key in candidate_keys:
        if key and key in index:
            return index[key]

    # Loose fallback: containment match on normalized names.
    base_key = _normalize_lookup_key(base_name)
    if base_key:
        best_name = None
        best_score = 0
        for mod_name in entries:
            alias_key = _normalize_lookup_key(mod_name)
            if not alias_key:
                continue
            if base_key == alias_key:
                return mod_name
            if base_key in alias_key or alias_key in base_key:
                score = min(len(base_key), len(alias_key))
                if score > best_score:
                    best_score = score
                    best_name = mod_name
        if best_name and best_score >= 6:
            return best_name

    return base_name


def _dedupe_preserve_order(items):
    output = []
    seen = set()
    for item in items:
        value = str(item or "").strip()
        if not value:
            continue
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        output.append(value)
    return output


def _append_added_removed_summary(lines, added_names, removed_names, singular_label, plural_label):
    cleaned_added = [str(name).strip() for name in added_names if str(name).strip()]
    cleaned_removed = [str(name).strip() for name in removed_names if str(name).strip()]

    if cleaned_added:
        label = singular_label if len(cleaned_added) == 1 else plural_label
        lines.append(f"Added {_format_name_list(cleaned_added)} {label}.")
    if cleaned_removed:
        label = singular_label if len(cleaned_removed) == 1 else plural_label
        lines.append(f"Removed {_format_name_list(cleaned_removed)} {label}.")


def generate_deterministic_update_overview(diff_payload, migration_mode=False) -> List[str]:
    mod_diff = diff_payload.get("mod_differences") or {}
    res_diff = diff_payload.get("resourcepack_differences") or {}
    shader_diff = diff_payload.get("shaderpack_differences") or {}
    mod_addition_breakdown = diff_payload.get("mod_addition_breakdown") or {}

    new_mod_names = _dedupe_preserve_order(
        _resolve_mod_display_label(name) for name in mod_addition_breakdown.get("newly_added", [])
    )
    reenabled_mod_names = _dedupe_preserve_order(
        _resolve_mod_display_label(name) for name in mod_addition_breakdown.get("reenabled_from_disabled", [])
    )
    removed_mod_names = _dedupe_preserve_order(
        _resolve_mod_display_label(name) for name in mod_diff.get("removed", [])
    )
    added_res_names = list(res_diff.get("added", []))
    removed_res_names = list(res_diff.get("removed", []))
    added_shader_names = list(shader_diff.get("added", []))
    removed_shader_names = list(shader_diff.get("removed", []))

    new_mod_count = len(new_mod_names)
    reenabled_mod_count = len(reenabled_mod_names)
    removed_mod_count = len(removed_mod_names)
    updated_mod_count = len(_extract_modified_names(mod_diff.get("modified", [])))

    added_res_count = len(added_res_names)
    removed_res_count = len(removed_res_names)
    updated_res_count = len(_extract_modified_names(res_diff.get("modified", [])))
    added_shader_count = len(added_shader_names)
    removed_shader_count = len(removed_shader_names)
    updated_shader_count = len(_extract_modified_names(shader_diff.get("modified", [])))

    lines = []
    if migration_mode:
        lines.append(f"Updated to Minecraft {diff_payload.get('mc_version')}.")

    current_version = str(diff_payload.get("current_version") or "")
    is_alpha_or_beta = bool(re.search(r"\b(alpha|beta)\b", current_version, re.IGNORECASE))

    if new_mod_count > 0:
        if new_mod_count == 1:
            lines.append(f"Added '{new_mod_names[0]}' mod.")
        else:
            lines.append(f"Added {_format_name_list(new_mod_names)} mods.")
    if reenabled_mod_count > 0:
        if is_alpha_or_beta:
            lines.append(f"Re-added some mods that have become available for {diff_payload.get('mc_version')}.")
        else:
            lines.append("Re-added some mods.")


    if removed_mod_count > 0:
        if migration_mode:
            lines.append(f"Temporarily removed incompatible mods: {_format_quoted_names(removed_mod_names)}.")
        else:
            if removed_mod_count == 1:
                lines.append(f"Removed '{removed_mod_names[0]}' mod.")
            else:
                lines.append(f"Removed {_format_name_list(removed_mod_names)} mods.")

    updated_categories = []
    if updated_mod_count > 0:
        updated_categories.append("mods")
    if updated_res_count > 0:
        updated_categories.append("resource packs")
    if updated_shader_count > 0:
        updated_categories.append("shaderpacks")

    if len(updated_categories) == 1:
        lines.append(f"Updated {updated_categories[0]}.")
    elif len(updated_categories) == 2:
        lines.append(f"Updated {updated_categories[0]} & {updated_categories[1]}.")
    elif len(updated_categories) > 2:
        lines.append(
            f"Updated {', '.join(updated_categories[:-1])}, & {updated_categories[-1]}."
        )

    _append_added_removed_summary(
        lines,
        added_res_names,
        removed_res_names,
        singular_label="resource pack",
        plural_label="resource packs",
    )

    _append_added_removed_summary(
        lines,
        added_shader_names,
        removed_shader_names,
        singular_label="shaderpack",
        plural_label="shaderpacks",
    )

    if not lines:
        lines.append("Maintenance update.")

    # Keep stable order but remove accidental duplicates.
    unique_lines = []
    seen = set()
    for line in lines:
        key = line.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        unique_lines.append(line)
    return unique_lines


def _normalize_llm_text_to_bullets(raw_text: str, max_lines: int):
    lines = []
    for raw_line in str(raw_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^[\-\*\u2022]\s*", "", line)
        line = re.sub(r"^\d+[.)]\s*", "", line)
        if line:
            normalized = line.strip()
            lines.append(f"- {normalized}")
    return lines[:max_lines]


def _normalize_config_llm_bullets(raw_text: str, max_lines: int):
    normalized = _normalize_llm_text_to_bullets(raw_text, max_lines=max(max_lines * 2, max_lines))
    valid = []
    invalid_count = 0
    required_suffix_pattern = re.compile(r"\[[^\[\]]+\]\.?$")

    for bullet in normalized:
        line = str(bullet or "").strip()
        if required_suffix_pattern.search(line):
            valid.append(line)
            if len(valid) >= max_lines:
                break
        else:
            invalid_count += 1

    return valid[:max_lines], invalid_count


def _normalize_redundant_config_context_tail(bullets: List[str]) -> List[str]:
    normalized = []
    label_suffix_pattern = re.compile(r"(\s*[:,-]?\s*\[[^\[\]]+\]\.?)$", flags=re.IGNORECASE)
    tail_pattern = re.compile(
        r"\s+(?:in|within|inside)\s+(?:the\s+)?"
        r"(?:[a-z0-9][a-z0-9\s_\-./&']{0,80}\s+)?"
        r"(?:mod\s+)?(?:config(?:uration)?s?|settings?|options?|overrides?)(?:\s+file)?\s*$",
        flags=re.IGNORECASE,
    )
    for raw_line in bullets:
        line = str(raw_line or "").strip()
        if not line:
            continue
        suffix_match = label_suffix_pattern.search(line)
        if not suffix_match:
            normalized.append(line)
            continue

        suffix = suffix_match.group(1)
        body = line[:suffix_match.start()]
        body = tail_pattern.sub("", body).rstrip()
        if not body:
            body = line[:suffix_match.start()].rstrip()
        line = f"{body}{suffix}"
        normalized.append(line)
    return normalized


def format_config_filename_as_title(filename: str) -> str:
    name = os.path.splitext(str(filename or "").strip())[0]
    if not name:
        return "Unknown"

    name = re.sub(r"[_\-.]+", " ", name)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    tokens = [token for token in name.split() if token]
    if not tokens:
        return "Unknown"

    return " ".join(token.capitalize() for token in tokens)


def _normalize_lookup_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _load_modlist_label_index():
    global _mod_label_index_cache
    if _mod_label_index_cache is not None:
        return _mod_label_index_cache

    index = {}
    entries = []
    try:
        active_mod_names = parse_active_projects(packwiz_mods_path, "name")
    except Exception:
        active_mod_names = []

    for raw_name in active_mod_names:
        name_no_side = re.sub(r"\s*\[[^\]]+\]\s*$", "", str(raw_name or "")).strip()
        mod_name = re.sub(r"\s*-\s*$", "", name_no_side).strip() or name_no_side
        if not mod_name:
            continue
        key = _normalize_lookup_key(mod_name)
        if not key:
            continue
        index.setdefault(key, mod_name)
        entries.append(mod_name)

    _mod_label_index_cache = {"index": index, "entries": entries}
    return _mod_label_index_cache


def _get_normalized_config_path_parts(path: str) -> List[str]:
    raw_path = str(path or "").replace("\\", "/").strip("/")
    parts = [p for p in raw_path.split("/") if p]
    if not parts:
        return []

    # Treat yosbr/config as the config root for label inference.
    if parts[0].lower() == "yosbr":
        if len(parts) > 1 and parts[1].lower() == "config":
            return parts[2:]
        return parts[1:]

    return parts


def derive_mod_display_label_from_config_path(path: str) -> str:
    normalized_parts = _get_normalized_config_path_parts(path)
    filename = normalized_parts[-1] if normalized_parts else derive_mod_label_from_config_path(path)
    stem = os.path.splitext(filename)[0].strip()
    parent = normalized_parts[-2] if len(normalized_parts) > 1 else ""
    top_folder = normalized_parts[0] if len(normalized_parts) > 1 else ""

    label_index = _load_modlist_label_index()
    index = label_index.get("index", {})
    entries = label_index.get("entries", [])

    candidate_keys = [
        _normalize_lookup_key(top_folder),
        _normalize_lookup_key(stem),
        _normalize_lookup_key(filename),
        _normalize_lookup_key(parent),
    ]
    for key in candidate_keys:
        if key and key in index:
            return index[key]

    # Loose fallback: match by containment on normalized keys.
    loose_keys = []
    top_folder_key = _normalize_lookup_key(top_folder)
    if top_folder_key:
        loose_keys.append(top_folder_key)
    stem_key = _normalize_lookup_key(stem)
    if stem_key and stem_key not in loose_keys:
        loose_keys.append(stem_key)

    for lookup_key in loose_keys:
        best_name = None
        best_score = 0
        for mod_name in entries:
            alias_key = _normalize_lookup_key(mod_name)
            if not alias_key:
                continue
            if lookup_key == alias_key:
                return mod_name
            if lookup_key in alias_key or alias_key in lookup_key:
                score = min(len(lookup_key), len(alias_key))
                if score > best_score:
                    best_score = score
                    best_name = mod_name
        if best_name and best_score >= 6:
            return best_name

    if top_folder:
        return format_config_filename_as_title(top_folder)

    return format_config_filename_as_title(filename)


def _build_config_label_maps(diff_payload):
    config_diff = diff_payload.get("config_differences") or {}
    line_diffs = list(config_diff.get("modified_line_diffs", []))
    moved_to_yosbr = list(config_diff.get("moved_to_yosbr", []))
    stem_to_label = {}
    alias_to_label = {}

    for entry in line_diffs:
        file_path = str(entry.get("path", "")).strip()
        if not file_path:
            continue

        config_filename = derive_mod_label_from_config_path(file_path)
        stem = os.path.splitext(config_filename)[0].strip().lower()
        title_label = format_config_filename_as_title(config_filename)
        resolved_label = derive_mod_display_label_from_config_path(file_path)

        if stem and resolved_label:
            stem_to_label[stem] = resolved_label

        if config_filename:
            alias_to_label[config_filename.strip().lower()] = resolved_label
        if stem:
            alias_to_label[stem] = resolved_label
        if title_label:
            alias_to_label[title_label.strip().lower()] = resolved_label
        if resolved_label:
            alias_to_label[resolved_label.strip().lower()] = resolved_label

    for move in moved_to_yosbr:
        move_to_path = str(move.get("to", "")).strip()
        if not move_to_path:
            continue
        config_filename = derive_mod_label_from_config_path(move_to_path)
        stem = os.path.splitext(config_filename)[0].strip().lower()
        title_label = format_config_filename_as_title(config_filename)
        resolved_label = derive_mod_display_label_from_config_path(move_to_path)

        if stem and resolved_label:
            stem_to_label[stem] = resolved_label

        if config_filename:
            alias_to_label[config_filename.strip().lower()] = resolved_label
        if stem:
            alias_to_label[stem] = resolved_label
        if title_label:
            alias_to_label[title_label.strip().lower()] = resolved_label
        if resolved_label:
            alias_to_label[resolved_label.strip().lower()] = resolved_label

    return stem_to_label, alias_to_label


def _normalize_config_change_labels(text: str, diff_payload) -> str:
    stem_to_label, _ = _build_config_label_maps(diff_payload)

    if not stem_to_label:
        return str(text or "")

    def _replace_label(match):
        label = str(match.group(1) or "").strip()
        lowered = label.lower()
        if lowered in stem_to_label:
            return f"[{stem_to_label[lowered]}]"
        return match.group(0)

    return re.sub(r"\[([^\]]+)\]", _replace_label, str(text or ""))


def _normalize_config_change_title_labels(text: str, diff_payload) -> str:
    _, alias_to_label = _build_config_label_maps(diff_payload)
    if not alias_to_label:
        return str(text or "")

    def _replace_label(match):
        label = str(match.group(1) or "").strip()
        lowered = label.lower()
        resolved_label = alias_to_label.get(lowered)
        if resolved_label:
            return f"[{resolved_label}]"
        return match.group(0)

    return re.sub(r"\[([^\]]+)\]", _replace_label, str(text or ""))


def _apply_yosbr_default_wording(text: str, diff_payload) -> str:
    config_diff = diff_payload.get("config_differences") or {}
    line_diffs = list(config_diff.get("modified_line_diffs", []))
    yosbr_labels = set()
    for entry in line_diffs:
        file_path = str(entry.get("path", "")).strip()
        if not file_path or not is_yosbr_config_path(file_path):
            continue
        yosbr_labels.add(derive_mod_display_label_from_config_path(file_path).strip().lower())

    if not yosbr_labels:
        return str(text or "")

    normalized_lines = []
    suffix_pattern = re.compile(r"(?::\s*)?\[([^\[\]]+)\]\.?$")

    for raw_line in str(text or "").splitlines():
        line = str(raw_line).strip()
        if not line:
            continue

        suffix_match = suffix_pattern.search(line)
        label = str(suffix_match.group(1)).strip().lower() if suffix_match else ""
        if label in yosbr_labels:
            if re.search(r"^-\s*changed\s+(?!default\b)", line, flags=re.IGNORECASE):
                line = re.sub(r"^-\s*changed\s+", "- Changed default ", line, count=1, flags=re.IGNORECASE)
            elif re.search(r"^-\s*updated\s+config\s+values\b", line, flags=re.IGNORECASE):
                line = re.sub(
                    r"^-\s*updated\s+config\s+values\b",
                    "- Updated default config values",
                    line,
                    count=1,
                    flags=re.IGNORECASE,
                )

        normalized_lines.append(line)

    return "\n".join(normalized_lines)


def _extract_config_bullet_labels(bullets: List[str]) -> set:
    labels = set()
    suffix_pattern = re.compile(r"(?::\s*)?\[([^\[\]]+)\]\.?$")
    for raw_line in bullets:
        line = str(raw_line or "").strip()
        if not line:
            continue
        match = suffix_pattern.search(line)
        if match:
            labels.add(str(match.group(1)).strip().lower())
    return labels


def _get_expected_config_labels(diff_payload) -> set:
    config_diff = diff_payload.get("config_differences") or {}
    modified_line_diffs = list(config_diff.get("modified_line_diffs", []))
    labels = set()
    if not modified_line_diffs:
        return labels

    for entry in modified_line_diffs:
        file_path = str(entry.get("path", "")).strip()
        if not file_path:
            continue
        mod_label = derive_mod_display_label_from_config_path(file_path).strip().lower()
        if mod_label:
            labels.add(mod_label)
    return labels


def _get_missing_config_labels_from_bullets(bullets: List[str], diff_payload) -> List[str]:
    expected_labels = _get_expected_config_labels(diff_payload)
    if not expected_labels:
        return []
    existing_labels = _extract_config_bullet_labels(bullets)
    return sorted(expected_labels - existing_labels)


def _get_expected_yosbr_moves(diff_payload) -> List[str]:
    config_diff = diff_payload.get("config_differences") or {}
    moved_to_yosbr = list(config_diff.get("moved_to_yosbr", []))
    moves = []
    seen = set()

    for move in moved_to_yosbr:
        from_path = str(move.get("from", "")).replace("\\", "/").strip("/")
        if not from_path:
            continue
        key = from_path.lower()
        if key in seen:
            continue
        seen.add(key)
        moves.append(from_path)
    return moves


def _get_missing_yosbr_moves_from_bullets(bullets: List[str], diff_payload) -> List[str]:
    expected_moves = _get_expected_yosbr_moves(diff_payload)
    if not expected_moves:
        return []

    lowered_bullets = [str(line or "").strip().lower() for line in bullets if str(line or "").strip()]
    missing = []
    for from_path in expected_moves:
        from_lower = from_path.lower()
        found = any(
            (from_lower in bullet) and ("yosbr" in bullet)
            for bullet in lowered_bullets
        )
        if not found:
            missing.append(from_path)
    return missing


def _normalize_yosbr_move_bullet_wording(bullets: List[str], diff_payload) -> List[str]:
    config_diff = diff_payload.get("config_differences") or {}
    moved_to_yosbr = list(config_diff.get("moved_to_yosbr", []))
    if not moved_to_yosbr:
        return list(bullets)

    move_map = {}
    for move in moved_to_yosbr:
        from_path = str(move.get("from", "")).replace("\\", "/").strip("/")
        to_path = str(move.get("to", "")).replace("\\", "/").strip("/")
        if not from_path:
            continue
        move_map[from_path.lower()] = {
            "from": from_path,
            "to": to_path,
            "label": derive_mod_display_label_from_config_path(to_path or from_path),
        }

    normalized = []
    suffix_pattern = re.compile(r"((?::\s*)?\[[^\[\]]+\]\.?)$")
    for raw_line in bullets:
        line = str(raw_line or "").strip()
        if not line:
            continue
        line_lower = line.lower()
        replaced = False

        for from_lower, meta in move_map.items():
            if from_lower not in line_lower:
                continue

            suffix_match = suffix_pattern.search(line)
            if suffix_match:
                label_suffix = suffix_match.group(1)
                line_body = line[:suffix_match.start()].rstrip()
            else:
                label_suffix = f": [{meta.get('label', 'Unknown')}]"
                line_body = line

            if "yosbr" not in line_body.lower():
                line_body = f"{line_body} (YOSBR)"

            line = f"{line_body}{label_suffix}"
            replaced = True
            break

        normalized.append(line if replaced else line)

    return normalized


def _filter_unexpected_yosbr_move_bullets(bullets: List[str], diff_payload) -> List[str]:
    expected_moves = [str(path).lower() for path in _get_expected_yosbr_moves(diff_payload)]
    filtered = []
    for raw_line in bullets:
        line = str(raw_line or "").strip()
        if not line:
            continue
        lowered = line.lower()
        is_move_bullet = ("moved " in lowered) and ("yosbr" in lowered)
        if not is_move_bullet:
            filtered.append(line)
            continue

        if not expected_moves:
            continue
        if any(expected_move in lowered for expected_move in expected_moves):
            filtered.append(line)

    return filtered


def _filter_unverified_config_operation_bullets(bullets: List[str], diff_payload) -> List[str]:
    config_diff = diff_payload.get("config_differences") or {}
    line_diffs = list(config_diff.get("modified_line_diffs", []))
    moved_to_yosbr = list(config_diff.get("moved_to_yosbr", []))

    has_removed_lines = False
    evidence_chunks = []
    for entry in line_diffs:
        removed_lines = [str(line).strip() for line in list(entry.get("removed_lines", [])) if str(line).strip()]
        added_lines = [str(line).strip() for line in list(entry.get("added_lines", [])) if str(line).strip()]
        if removed_lines:
            has_removed_lines = True
        evidence_chunks.extend(removed_lines)
        evidence_chunks.extend(added_lines)

    has_move_entries = bool(moved_to_yosbr)
    evidence_text = "\n".join(evidence_chunks).lower()
    operation_pattern = re.compile(r"\b(removed|moved|reordered|re-ordered|replaced|relocated|swapped)\b")
    set_key_pattern = re.compile(r'\bset\s+"([^"]+)"')

    filtered = []
    for raw_line in bullets:
        line = str(raw_line or "").strip()
        if not line:
            continue
        lowered = line.lower()

        # Prevent hallucinated move/remove/reorder language when the source diff has no removals or yosbr moves.
        if not has_removed_lines and not has_move_entries and operation_pattern.search(lowered):
            continue

        # If a bullet claims a specific quoted key was set, keep it only when that key appears in diff evidence.
        set_key_match = set_key_pattern.search(lowered)
        if set_key_match:
            quoted_key = str(set_key_match.group(1) or "").strip().lower()
            if quoted_key and quoted_key not in evidence_text:
                continue

        filtered.append(line)

    return filtered


def derive_mod_label_from_config_path(path: str) -> str:
    raw_path = str(path or "").replace("\\", "/").strip("/")
    if not raw_path:
        return "Unknown"

    parts = [p for p in raw_path.split("/") if p]
    filename = parts[-1] if parts else raw_path
    return filename


def is_yosbr_config_path(path: str) -> bool:
    normalized = str(path or "").replace("\\", "/").strip("/").lower()
    return normalized.startswith("yosbr/")


def get_config_change_verb(path: str) -> str:
    return "Changed default" if is_yosbr_config_path(path) else "Changed"


def _strip_double_slash_comments(line: str) -> str:
    text = str(line or "")
    output = []
    in_string = False
    escaped = False

    for i, ch in enumerate(text):
        if escaped:
            output.append(ch)
            escaped = False
            continue

        if ch == "\\" and in_string:
            output.append(ch)
            escaped = True
            continue

        if ch == '"':
            output.append(ch)
            in_string = not in_string
            continue

        if not in_string and ch == "/" and i + 1 < len(text) and text[i + 1] == "/":
            break

        output.append(ch)

    return "".join(output)


def _extract_array_item_string_value(raw_line: str) -> Optional[str]:
    line = str(raw_line or "").strip()
    match = re.match(r'^\s*"((?:[^"\\]|\\.)*)"\s*,?\s*$', line)
    if not match:
        return None
    raw_value = match.group(1)
    try:
        return bytes(raw_value, "utf-8").decode("unicode_escape")
    except Exception:
        return raw_value


def _build_json_array_value_section_index(relative_config_path: str):
    normalized_rel = str(relative_config_path or "").replace("\\", "/").strip("/")
    if not normalized_rel:
        return {}

    _, ext = os.path.splitext(normalized_rel.lower())
    if ext not in (".json", ".json5"):
        return {}

    config_file_path = os.path.join(packwiz_path, "config", *normalized_rel.split("/"))
    if not os.path.isfile(config_file_path):
        return {}

    try:
        with open(config_file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return {}

    section_index = {}
    container_stack = []

    def _pop_matching_container(container_type: str):
        for stack_idx in range(len(container_stack) - 1, -1, -1):
            if container_stack[stack_idx]["type"] == container_type:
                container_stack.pop(stack_idx)
                return
        if container_stack:
            container_stack.pop()

    for raw_line in lines:
        line = _strip_double_slash_comments(raw_line).strip()
        if not line:
            continue

        string_value = _extract_array_item_string_value(line)
        if string_value is not None:
            section_names = [item["name"] for item in container_stack if item.get("name")]
            if section_names:
                section_path = ".".join(section_names)
                lowered = string_value.lower()
                section_list = section_index.setdefault(lowered, [])
                if section_path not in section_list:
                    section_list.append(section_path)

        key_open_match = re.match(r'^"([^"]+)"\s*:\s*([\[{])', line)
        if key_open_match:
            key_name = key_open_match.group(1)
            opener = key_open_match.group(2)
            container_stack.append(
                {
                    "type": "array" if opener == "[" else "object",
                    "name": key_name,
                }
            )

        line_no_strings = re.sub(r'"(?:[^"\\]|\\.)*"', '""', line)
        for ch in line_no_strings:
            if ch == "]":
                _pop_matching_container("array")
            elif ch == "}":
                _pop_matching_container("object")

    return section_index


def _find_config_sections_for_line(file_path: str, raw_line: str, section_index_cache) -> List[str]:
    value = _extract_array_item_string_value(raw_line)
    if not value:
        return []

    relative_config_path = str(file_path or "").replace("\\", "/").strip("/")
    if relative_config_path not in section_index_cache:
        section_index_cache[relative_config_path] = _build_json_array_value_section_index(relative_config_path)

    section_index = section_index_cache.get(relative_config_path) or {}
    return list(section_index.get(value.lower(), []))


def _format_line_with_section_context(file_path: str, raw_line: str, section_index_cache) -> str:
    base_line = str(raw_line or "").strip()
    if not base_line:
        return base_line

    sections = _find_config_sections_for_line(file_path, base_line, section_index_cache)
    if not sections:
        return base_line

    if len(sections) == 1:
        return f"{base_line} (section: {sections[0]})"
    return f"{base_line} (sections: {', '.join(sections)})"


def _format_added_or_removed_list_value_bullet(action: str, file_path: str, raw_line: str, mod_label: str, section_index_cache) -> Optional[str]:
    value = _extract_array_item_string_value(raw_line)
    if not value:
        return None

    action_label = str(action or "").strip()
    if is_yosbr_config_path(file_path):
        action_label = f"{action_label} default"

    sections = _find_config_sections_for_line(file_path, raw_line, section_index_cache)
    display_value = value[5:] if value.startswith("file/") else value
    if sections:
        if len(sections) == 1:
            return f"- {action_label} {display_value} to section {sections[0]}: [{mod_label}]."
        return f"- {action_label} {display_value} to sections {', '.join(sections)}: [{mod_label}]."
    return f"- {action_label} {display_value}: [{mod_label}]."


def _is_fancymenu_customization_path(path: str) -> bool:
    normalized = str(path or "").replace("\\", "/").strip("/").lower()
    if not normalized:
        return False
    wrapped = f"/{normalized}/"
    return "/fancymenu/customization/" in wrapped


def build_config_label_title_prompt(text: str, diff_payload, max_items=20):
    config_diff = diff_payload.get("config_differences") or {}
    modified_line_diffs = list(config_diff.get("modified_line_diffs", []))
    moved_to_yosbr = list(config_diff.get("moved_to_yosbr", []))

    mapping_lines = []
    for entry in modified_line_diffs[:max_items]:
        path = str(entry.get("path", "")).strip()
        if not path:
            continue
        filename = derive_mod_label_from_config_path(path)
        resolved_label = derive_mod_display_label_from_config_path(path)
        mapping_lines.append(f"- {filename} => {resolved_label}")
    remaining_capacity = max(max_items - len(mapping_lines), 0)
    if remaining_capacity > 0:
        for move in moved_to_yosbr[:remaining_capacity]:
            to_path = str(move.get("to", "")).strip()
            if not to_path:
                continue
            filename = derive_mod_label_from_config_path(to_path)
            resolved_label = derive_mod_display_label_from_config_path(to_path)
            mapping_lines.append(f"- {filename} => {resolved_label}")

    mapping = "\n".join(mapping_lines) if mapping_lines else "none"
    return (
        "Rewrite only bracket labels in these bullets.\n"
        "Keep the bullets, wording, order, and punctuation unchanged.\n"
        "Only update text inside square brackets to the mapped mod labels from the mod list.\n"
        "If a bracket label already matches the mapped mod label, keep it.\n"
        "Output only bullet lines.\n\n"
        "Filename to mod label mapping:\n"
        f"{mapping}\n\n"
        "Bullets:\n"
        f"{str(text or '').strip()}\n"
    )


def build_config_changes_prompt(diff_payload, max_items=12):
    config_diff = diff_payload.get("config_differences") or {}
    modified_line_diffs = list(config_diff.get("modified_line_diffs", []))
    moved_to_yosbr = list(config_diff.get("moved_to_yosbr", []))
    section_index_cache = {}

    def _render_line_changes(items):
        if not items:
            return "none"
        rendered = []
        for entry in items[:max_items]:
            file_path = str(entry.get("path", "")).strip()
            rendered.append(f"File: {file_path}")
            if _is_fancymenu_customization_path(file_path):
                rendered.append("Detail policy: Do not list specific option names or exact values for this file.")
                rendered.append("Use a generic summary only (example: 'Adjusted FancyMenu customizations').")
                rendered.append("")
                continue
            removed_lines = list(entry.get("removed_lines", []))[:8]
            added_lines = list(entry.get("added_lines", []))[:8]
            rendered.append("Verified line deltas (source of truth):")
            if removed_lines:
                rendered.append("Removed lines:")
                rendered.extend(
                    f"- {_format_line_with_section_context(file_path, line, section_index_cache)}"
                    for line in removed_lines
                )
            else:
                rendered.append("Removed lines: none")
            if added_lines:
                rendered.append("Added lines:")
                rendered.extend(
                    f"- {_format_line_with_section_context(file_path, line, section_index_cache)}"
                    for line in added_lines
                )
            else:
                rendered.append("Added lines: none")
            rendered.append("")
        return "\n".join(rendered).strip()

    def _render_path_to_label(line_items, move_items):
        rendered = []
        seen_paths = set()

        for entry in line_items[:max_items]:
            path = str(entry.get("path", "")).strip()
            if not path:
                continue
            lowered = path.lower()
            if lowered in seen_paths:
                continue
            seen_paths.add(lowered)
            label = derive_mod_display_label_from_config_path(path)
            rendered.append(f"- {path} => {label}")

        remaining_capacity = max(max_items - len(rendered), 0)
        if remaining_capacity > 0:
            for move in move_items[:remaining_capacity]:
                path = str(move.get("to", "")).strip()
                if not path:
                    continue
                lowered = path.lower()
                if lowered in seen_paths:
                    continue
                seen_paths.add(lowered)
                label = derive_mod_display_label_from_config_path(path)
                rendered.append(f"- {path} => {label}")

        if not rendered:
            return "none"
        return "\n".join(rendered)

    def _render_yosbr_moves(items):
        if not items:
            return "none"
        rendered = []
        for entry in items[:max_items]:
            from_path = str(entry.get("from", "")).strip()
            if not from_path:
                continue
            if entry.get("content_changed"):
                rendered.append(f"- {from_path} (moved to yosbr, defaults also changed)")
            else:
                rendered.append(f"- {from_path} (moved to yosbr)")
        return "\n".join(rendered) if rendered else "none"

    return (
        "Write concise end-user facing config change bullets for a Minecraft modpack changelog.\n"
        "Output only bullet lines. Each line must start with '- '.\n"
        "Keep wording factual, short, and natural. No markdown headers and no counts.\n"
        "Use varied sentence openings; do not repeat the same lead-in phrase on every bullet.\n"
        "Treat 'Verified line deltas' as the only source of truth.\n"
        "Only describe facts directly supported by those deltas.\n"
        "If a value appears in both old and new file state, treat it as unchanged and do not mention it.\n"
        "Do not claim moves/removals/reorders/replacements unless that operation is explicitly shown in removed+added evidence or YOSBR move entries.\n"
        "If a file says 'Removed lines: none', do not use moved/removed/reordered/replaced wording for that file.\n"
        "Summarize player-facing impact when clear.\n"
        "Files under 'yosbr/' are defaults applied only on first launch.\n"
        "Do not treat regular config -> yosbr moves as removals.\n"
        "For each item listed under 'Regular config files moved to yosbr', include one explicit bullet that mentions the source path and YOSBR.\n"
        "For added/removed list entries, explicitly mention the section path when provided in '(section: ...)' context.\n"
        "Do not write generic lines like 'updated config files'.\n"
        "Avoid redundant tails like 'in <mod> config/settings/overrides'; keep wording focused on the actual value/behavior change.\n"
        "For files under 'fancymenu/customization', do not mention specific keys/values; summarize those changes generically but do mention which menu was changes.\n"
        "Do not write about added/removed config files.\n"
        "Summarize in-file setting/value changes from modified files, and also include yosbr move bullets.\n"
        "Each bullet must end with '[Mod Name]'.\n"
        "Use the file-to-mod mapping below when possible.\n"
        "Preferred style examples:\n"
        "- Ads are now disabled in the client panel: [Resourcify]\n"
        "- Switched the button URL to the new wiki format: [Simple Discord RPC]\n"
        "- Set \"coloredText\" to false for cleaner menu text: [Breakneck Menu]\n\n"
        "YOSBR style example:\n"
        "- Set default \"coloredText\" to false by default: [Breakneck Menu]\n\n"
        "YOSBR move style example:\n"
        "- Moved <source path> to YOSBR so defaults are applied on first launch: [Mod Name]\n\n"
        f"Current version: {diff_payload.get('current_version')}\n"
        f"Compared from: {diff_payload.get('previous_version')}\n"
        f"Minecraft: {diff_payload.get('mc_version')}\n\n"
        "File-to-mod mapping:\n"
        f"{_render_path_to_label(modified_line_diffs, moved_to_yosbr)}\n\n"
        "Regular config files moved to yosbr:\n"
        f"{_render_yosbr_moves(moved_to_yosbr)}\n\n"
        "Modified config evidence (verified line deltas):\n"
        f"{_render_line_changes(modified_line_diffs)}\n"
    )


def generate_config_changes_fallback_from_line_diffs(diff_payload, max_lines=8) -> str:
    config_diff = diff_payload.get("config_differences") or {}
    line_diffs = list(config_diff.get("modified_line_diffs", []))
    bullets = []
    section_index_cache = {}

    for entry in line_diffs:
        file_path = str(entry.get("path", "")).strip()
        mod_label = derive_mod_display_label_from_config_path(file_path)
        if _is_fancymenu_customization_path(file_path):
            if is_yosbr_config_path(file_path):
                bullets.append(f"- Adjusted default FancyMenu customizations: [{mod_label}].")
            else:
                bullets.append(f"- Adjusted FancyMenu customizations: [{mod_label}].")
            if len(bullets) >= max_lines:
                break
            continue
        added_lines = entry.get("added_lines", [])
        removed_lines = entry.get("removed_lines", [])
        candidate_lines = list(added_lines) + list(removed_lines)
        entry_started_with_bullet_count = len(bullets)

        for raw_line in added_lines:
            bullet = _format_added_or_removed_list_value_bullet(
                action="Added",
                file_path=file_path,
                raw_line=raw_line,
                mod_label=mod_label,
                section_index_cache=section_index_cache,
            )
            if bullet:
                bullets.append(bullet)
            if len(bullets) >= max_lines:
                break
        if len(bullets) >= max_lines:
            break

        for raw_line in removed_lines:
            bullet = _format_added_or_removed_list_value_bullet(
                action="Removed",
                file_path=file_path,
                raw_line=raw_line,
                mod_label=mod_label,
                section_index_cache=section_index_cache,
            )
            if bullet:
                bullets.append(bullet)
            if len(bullets) >= max_lines:
                break
        if len(bullets) >= max_lines:
            break

        keys = []
        for raw_line in candidate_lines:
            line = str(raw_line).strip()
            if not line or line.startswith(("#", "//", ";", "/*", "*")):
                continue
            key_match = re.match(r'^["\']?([A-Za-z0-9_.-]+)["\']?\s*[:=]', line)
            if key_match:
                key = key_match.group(1)
                if key not in keys:
                    keys.append(key)
            if len(keys) >= 4:
                break

        if len(bullets) > entry_started_with_bullet_count:
            continue

        change_verb = get_config_change_verb(file_path)
        if keys:
            keys_str = ", ".join(keys)
            bullets.append(f"- {change_verb} {keys_str}: [{mod_label}].")
        else:
            if is_yosbr_config_path(file_path):
                bullets.append(f"- Updated default config values: [{mod_label}].")
            else:
                bullets.append(f"- Updated config values: [{mod_label}].")
        if len(bullets) >= max_lines:
            break

    return "\n".join(bullets)


def generate_removed_config_file_bullets(diff_payload, max_lines=8) -> List[str]:
    config_diff = diff_payload.get("config_differences") or {}
    removed_paths = list(config_diff.get("removed", []))
    moved_to_yosbr = list(config_diff.get("moved_to_yosbr", []))
    moved_from_paths = {
        str(entry.get("from", "")).replace("\\", "/").strip("/").lower()
        for entry in moved_to_yosbr
        if str(entry.get("from", "")).strip()
    }
    bullets = []
    seen = set()

    for raw_path in removed_paths:
        relative_path = str(raw_path or "").replace("\\", "/").strip("/")
        if not relative_path:
            continue

        dedupe_key = relative_path.lower()
        if dedupe_key in seen:
            continue
        if dedupe_key in moved_from_paths:
            continue
        seen.add(dedupe_key)

        mod_label = derive_mod_display_label_from_config_path(relative_path)
        bullets.append(f"- Removed config file {relative_path}: [{mod_label}].")
        if len(bullets) >= max_lines:
            break

    return bullets


def generate_yosbr_default_move_bullets(diff_payload, max_lines=8) -> List[str]:
    config_diff = diff_payload.get("config_differences") or {}
    moved_to_yosbr = list(config_diff.get("moved_to_yosbr", []))
    bullets = []
    seen = set()

    for move in moved_to_yosbr:
        from_path = str(move.get("from", "")).replace("\\", "/").strip("/")
        to_path = str(move.get("to", "")).replace("\\", "/").strip("/")
        if not from_path and not to_path:
            continue

        dedupe_key = f"{from_path.lower()}->{to_path.lower()}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        mod_label = derive_mod_display_label_from_config_path(to_path or from_path)
        bullets.append(
            f"- Moved {from_path} to {to_path} so it is now applied as a default on first launch: [{mod_label}]."
        )
        if len(bullets) >= max_lines:
            break

    return bullets


def format_config_change_labels_with_llm(text: str, diff_payload, settings) -> Optional[str]:
    if str(settings.auto_config_provider).lower() != "ollama":
        return None

    prompt = build_config_label_title_prompt(
        text,
        diff_payload,
        max_items=settings.auto_config_max_items,
    )
    max_attempts = 3
    for attempt in range(max_attempts):
        num_predict = 200 + (attempt * 120)
        try:
            response = requests.post(
                settings.auto_config_endpoint,
                json={
                    "model": settings.auto_config_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0,
                        "num_predict": num_predict,
                    },
                },
                timeout=int(settings.auto_config_timeout_seconds),
            )
            response.raise_for_status()
            response_json = response.json()
            bullet_lines, invalid_count = _normalize_config_llm_bullets(
                response_json.get("response", ""),
                max_lines=settings.auto_config_max_lines,
            )
            done_reason = str(response_json.get("done_reason", "") or "").strip().lower()
            if bullet_lines and invalid_count == 0:
                return "\n".join(bullet_lines)

            needs_retry = (
                attempt + 1 < max_attempts
                and (invalid_count > 0 or done_reason in ("length", "max_tokens"))
            )
            if needs_retry:
                print("[Changelog] LLM label formatting returned incomplete output. Retrying...")
        except Exception as ex:
            print(f"[Changelog] LLM label formatting pass failed: {ex}")
            break

    return None


def _build_config_change_payload_for_file(diff_payload, file_path: str):
    normalized_path = str(file_path or "").replace("\\", "/").strip("/")
    if not normalized_path:
        return None

    base_config_diff = dict((diff_payload or {}).get("config_differences") or {})
    all_line_diffs = list(base_config_diff.get("modified_line_diffs", []))
    all_moves = list(base_config_diff.get("moved_to_yosbr", []))

    selected_line_diffs = []
    for entry in all_line_diffs:
        entry_path = str(entry.get("path", "")).replace("\\", "/").strip("/")
        if entry_path == normalized_path:
            selected_line_diffs.append(entry)

    if len(selected_line_diffs) > 1:
        merged_removed = []
        merged_added = []
        merged_previous_content = ""
        merged_current_content = ""
        seen_removed = set()
        seen_added = set()
        for entry in selected_line_diffs:
            if not merged_previous_content:
                merged_previous_content = str(entry.get("previous_content", ""))
            if not merged_current_content:
                merged_current_content = str(entry.get("current_content", ""))
            for line in list(entry.get("removed_lines", [])):
                key = str(line).strip().lower()
                if not key or key in seen_removed:
                    continue
                seen_removed.add(key)
                merged_removed.append(str(line).strip())
            for line in list(entry.get("added_lines", [])):
                key = str(line).strip().lower()
                if not key or key in seen_added:
                    continue
                seen_added.add(key)
                merged_added.append(str(line).strip())
        selected_line_diffs = [
            {
                "path": normalized_path,
                "removed_lines": merged_removed[:20],
                "added_lines": merged_added[:20],
                "previous_content": merged_previous_content,
                "current_content": merged_current_content,
            }
        ]

    selected_moves = []
    for move in all_moves:
        move_to_path = str(move.get("to", "")).replace("\\", "/").strip("/")
        if move_to_path == normalized_path:
            selected_moves.append(move)

    if not selected_line_diffs and not selected_moves:
        return None

    single_payload = dict(diff_payload or {})
    config_diff = dict(base_config_diff)
    config_diff["added"] = []
    config_diff["removed"] = []
    config_diff["modified"] = [normalized_path]
    config_diff["modified_line_diffs"] = selected_line_diffs
    config_diff["moved_to_yosbr"] = selected_moves
    single_payload["config_differences"] = config_diff
    return single_payload


def _generate_config_change_item_with_llm(item_payload, settings, max_lines: int) -> List[str]:
    if max_lines <= 0:
        return []

    base_prompt = build_config_changes_prompt(
        item_payload,
        max_items=1,
    )
    max_attempts = 4
    best_bullets = []
    best_missing_score = None
    missing_labels_for_retry = []
    missing_moves_for_retry = []
    try:
        llm_temperature = float(getattr(settings, "auto_config_temperature", 0.25))
    except (TypeError, ValueError):
        llm_temperature = 0.25
    llm_temperature = max(0.0, min(llm_temperature, 2.0))

    for attempt in range(max_attempts):
        num_predict = 200 + (attempt * 140)
        coverage_hint = ""
        if missing_labels_for_retry or missing_moves_for_retry:
            coverage_hint_parts = []
            if missing_labels_for_retry:
                missing_label_render = "\n".join(f"- {label}" for label in missing_labels_for_retry[:6])
                coverage_hint_parts.append(
                    "Ensure at least one bullet for each missing label below.\n"
                    "Keep exact label spelling inside [] for these:\n"
                    f"{missing_label_render}\n"
                )
            if missing_moves_for_retry:
                missing_move_render = "\n".join(f"- {move}" for move in missing_moves_for_retry[:6])
                coverage_hint_parts.append(
                    "Ensure one bullet for each missing yosbr move below.\n"
                    "Mention each exact source path and make clear it moved to YOSBR:\n"
                    f"{missing_move_render}\n"
                )
            coverage_hint = (
                "\nCoverage retry requirement:\n"
                f"{''.join(coverage_hint_parts)}"
            )

        prompt = f"{base_prompt}{coverage_hint}"
        try:
            response = requests.post(
                settings.auto_config_endpoint,
                json={
                    "model": settings.auto_config_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": llm_temperature,
                        "num_predict": num_predict,
                    },
                },
                timeout=int(settings.auto_config_timeout_seconds),
            )
            response.raise_for_status()
            response_json = response.json()
            bullet_lines, invalid_count = _normalize_config_llm_bullets(
                response_json.get("response", ""),
                max_lines=max_lines,
            )
            done_reason = str(response_json.get("done_reason", "") or "").strip().lower()
            if bullet_lines and invalid_count == 0:
                normalized = _normalize_config_change_labels("\n".join(bullet_lines), item_payload)
                titled = format_config_change_labels_with_llm(normalized, item_payload, settings)
                if titled:
                    llm_text = _apply_yosbr_default_wording(
                        _normalize_config_change_title_labels(titled, item_payload),
                        item_payload,
                    )
                else:
                    llm_text = _apply_yosbr_default_wording(
                        _normalize_config_change_title_labels(normalized, item_payload),
                        item_payload,
                    )

                llm_bullets = _normalize_llm_text_to_bullets(llm_text, max_lines=max_lines)
                llm_bullets = _normalize_yosbr_move_bullet_wording(llm_bullets, item_payload)
                llm_bullets = _filter_unexpected_yosbr_move_bullets(llm_bullets, item_payload)
                llm_bullets = _filter_unverified_config_operation_bullets(llm_bullets, item_payload)
                llm_bullets = _normalize_redundant_config_context_tail(llm_bullets)
                missing_labels = _get_missing_config_labels_from_bullets(llm_bullets, item_payload)
                missing_moves = _get_missing_yosbr_moves_from_bullets(llm_bullets, item_payload)
                missing_score = len(missing_labels) + len(missing_moves)

                if not best_bullets or best_missing_score is None or missing_score < best_missing_score:
                    best_bullets = list(llm_bullets)
                    best_missing_score = missing_score

                if not missing_labels and not missing_moves:
                    return llm_bullets

                if attempt + 1 < max_attempts:
                    missing_labels_for_retry = list(missing_labels)
                    missing_moves_for_retry = list(missing_moves)
                    print("[Changelog] Per-item LLM config output missed required coverage. Retrying...")
                    continue

                return best_bullets

            needs_retry = (
                attempt + 1 < max_attempts
                and (invalid_count > 0 or done_reason in ("length", "max_tokens"))
            )
            if needs_retry:
                print("[Changelog] Per-item LLM config generation returned incomplete output. Retrying...")
        except Exception as ex:
            print(f"[Changelog] Per-item LLM config change generation failed: {ex}")
            break

    return best_bullets


def generate_config_changes_with_llm(diff_payload, settings, max_lines: Optional[int] = None) -> Optional[str]:
    if str(settings.auto_config_provider).lower() != "ollama":
        print(f"[Changelog] Unsupported auto_config_provider '{settings.auto_config_provider}'.")
        return None

    effective_max_lines = int(max_lines) if max_lines is not None else int(settings.auto_config_max_lines)
    if effective_max_lines <= 0:
        return None

    config_diff = diff_payload.get("config_differences") or {}
    modified_line_diffs = list(config_diff.get("modified_line_diffs", []))
    moved_to_yosbr = list(config_diff.get("moved_to_yosbr", []))

    ordered_paths = []
    seen_paths = set()

    def _append_path(raw_path):
        normalized = str(raw_path or "").replace("\\", "/").strip("/")
        if not normalized:
            return
        lowered = normalized.lower()
        if lowered in seen_paths:
            return
        seen_paths.add(lowered)
        ordered_paths.append(normalized)

    for entry in moved_to_yosbr:
        _append_path(entry.get("to", ""))
    for entry in modified_line_diffs:
        _append_path(entry.get("path", ""))

    change_items = []
    for file_path in ordered_paths:
        payload = _build_config_change_payload_for_file(diff_payload, file_path)
        if payload:
            change_items.append(payload)

    if not change_items:
        return None

    aggregated_bullets = []
    seen = set()

    for item_payload in change_items:
        remaining_budget = effective_max_lines - len(aggregated_bullets)
        if remaining_budget <= 0:
            break

        # One prompt per modified config file; allow each file to emit as many bullets as budget allows.
        item_bullets = _generate_config_change_item_with_llm(
            item_payload,
            settings,
            max_lines=remaining_budget,
        )
        for bullet in item_bullets:
            line = str(bullet or "").strip()
            if not line:
                continue
            dedupe_key = re.sub(r"[\s\.:;,\-]+$", "", line.lower())
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            aggregated_bullets.append(line)
            if len(aggregated_bullets) >= effective_max_lines:
                break

    if not aggregated_bullets:
        return None
    return "\n".join(aggregated_bullets)


def maybe_generate_update_overview(changelog_path, diff_payload):
    if not diff_payload:
        print("[Changelog] No diff payload available. Skipping auto summary.")
        return

    with open(changelog_path, "r", encoding="utf-8") as f:
        changelog_yml = yaml.load(f) or {}

    existing_overview = changelog_yml.get("Update overview")
    if existing_overview and not settings.auto_summary_overwrite_existing:
        print("[Changelog] 'Update overview' already exists. Skipping auto summary.")
        return

    summary_lines = generate_deterministic_update_overview(
        diff_payload,
        migration_mode=bool(settings.migrate_minecraft_version),
    )
    changelog_yml["Update overview"] = summary_lines
    with open(changelog_path, "w", encoding="utf-8") as f:
        yaml.dump(changelog_yml, f)

    print(f"[Changelog] Wrote deterministic 'Update overview' in {os.path.basename(changelog_path)}.")


def maybe_generate_config_changes(changelog_path, diff_payload):
    if not diff_payload:
        print("[Changelog] No diff payload available. Skipping config change generation.")
        return

    config_diff = diff_payload.get("config_differences") or {}
    modified_line_diffs = list(config_diff.get("modified_line_diffs", []))
    moved_to_yosbr_entries = list(config_diff.get("moved_to_yosbr", []))
    use_llm = uses_llm_config_changes(settings)
    if use_llm:
        moved_to_yosbr_bullets = []
    else:
        moved_to_yosbr_bullets = generate_yosbr_default_move_bullets(
            diff_payload,
            max_lines=settings.auto_config_max_lines,
        )
    remaining_after_yosbr = max(int(settings.auto_config_max_lines) - len(moved_to_yosbr_bullets), 0)
    include_removed_config_files = bool(getattr(settings, "auto_config_include_removed_files", True))
    if include_removed_config_files and remaining_after_yosbr > 0:
        removed_config_file_bullets = generate_removed_config_file_bullets(
            diff_payload,
            max_lines=remaining_after_yosbr,
        )
    else:
        removed_config_file_bullets = []

    with open(changelog_path, "r", encoding="utf-8") as f:
        changelog_yml = yaml.load(f) or {}

    existing_config_changes = changelog_yml.get("Config Changes")
    existing_config_text = str(existing_config_changes or "").strip()
    is_default_placeholder = existing_config_text in ("- : [mod], [Client]", "")
    if existing_config_changes and not is_default_placeholder and not settings.auto_config_overwrite_existing:
        print("[Changelog] 'Config Changes' already exists. Skipping auto generation.")
        return

    if not modified_line_diffs and not removed_config_file_bullets and not moved_to_yosbr_bullets and not moved_to_yosbr_entries:
        changelog_yml["Config Changes"] = ""
        with open(changelog_path, "w", encoding="utf-8") as f:
            yaml.dump(changelog_yml, f)
        print(f"[Changelog] Cleared 'Config Changes' (no config changes detected) in {os.path.basename(changelog_path)}.")
        return

    final_bullets = list(moved_to_yosbr_bullets) + list(removed_config_file_bullets)
    source_parts = []
    if moved_to_yosbr_bullets:
        source_parts.append("yosbr-defaults")
    if removed_config_file_bullets:
        source_parts.append("removed-files")

    remaining_line_budget = max(int(settings.auto_config_max_lines) - len(final_bullets), 0)
    line_change_text = None

    has_llm_line_source = bool(modified_line_diffs or moved_to_yosbr_entries)
    if has_llm_line_source and remaining_line_budget > 0:
        if use_llm:
            line_change_text = generate_config_changes_with_llm(
                diff_payload,
                settings,
                max_lines=remaining_line_budget,
            )
            if line_change_text:
                source_parts.append("LLM")
            else:
                print("[Changelog] LLM output was empty or failed after retries. Skipping deterministic fallback.")

        if not line_change_text and not use_llm:
            line_change_text = generate_config_changes_fallback_from_line_diffs(
                diff_payload,
                max_lines=remaining_line_budget,
            )
            if line_change_text:
                source_parts.append("fallback")

    if line_change_text and remaining_line_budget > 0:
        final_bullets.extend(
            _normalize_llm_text_to_bullets(line_change_text, max_lines=remaining_line_budget)
        )

    if not final_bullets:
        print("[Changelog] Skipping 'Config Changes': no usable output from LLM or fallback.")
        return

    changelog_yml["Config Changes"] = LiteralScalarString("\n".join(final_bullets))
    with open(changelog_path, "w", encoding="utf-8") as f:
        yaml.dump(changelog_yml, f)

    source_label = " + ".join(source_parts) if source_parts else "unknown"
    print(f"[Changelog] Auto-generated 'Config Changes' via {source_label} in {os.path.basename(changelog_path)}.")


def download_missing_comparison_files():
    if not settings.download_comparison_files:
        return

    if settings.github_auth:
        github_token = input("Your personal access token: ")
    else:
        github_token = None

    async def download_compare_files_async(input_version, destination, tag_mc_ver):
        print(f"Downloading {input_version} comparison files.")
        if settings.breakneck_fixes and is_version_in_range(input_version, "4.0.0-beta.3", "4.4.0-beta.1"):
            packwiz_root = f"Packwiz/{tag_mc_ver}"
        else:
            packwiz_root = "Packwiz"

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
            version = str(changelog_factory.get_changelog_value(changelog, "version"))
            tag_mc_ver = str(changelog_factory.get_changelog_value(changelog, "mc_version"))
            version_path = os.path.join(tempgit_path, version)
            missing_compare_data = (
                not os.path.isdir(os.path.join(version_path, "mods"))
                or not os.path.isdir(os.path.join(version_path, "resourcepacks"))
                or not os.path.isdir(os.path.join(version_path, "shaderpacks"))
                or not os.path.isdir(os.path.join(version_path, "config"))
            )
            if version != pack_version and (not os.path.exists(version_path) or missing_compare_data):
                os.makedirs(version_path, exist_ok=True)
                try:
                    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
                    asyncio.run(download_compare_files_async(version, version_path, tag_mc_ver))
                except Exception as ex:
                    print(ex)


def run_changelog_auto_generation():
    os.chdir(git_path)
    changelog_path = os.path.join(changelog_dir_path, f"{pack_version}+{minecraft_version}.yml")
    if not os.path.isfile(changelog_path):
        ensure_changelog_yml(pack_version, minecraft_version, active_mod_loader, mod_loader_version)

    diff_payload = changelog_factory.get_current_pack_diff_payload(
        target_version=pack_version,
        mc_version=minecraft_version,
        tempgit_path=tempgit_path,
        packwiz_path=packwiz_path,
    )
    if settings.auto_generate_update_overview or settings.generate_update_summary_only:
        maybe_generate_update_overview(changelog_path, diff_payload)
    if settings.auto_generate_config_changes or settings.generate_update_summary_only:
        maybe_generate_config_changes(changelog_path, diff_payload)


def clear_stored_repository_data():
    repo_data_paths = [tempgit_path, prev_release]
    for path in repo_data_paths:
        if os.path.isdir(path):
            rmtree(path)
        os.makedirs(path, exist_ok=True)
        print(f"[RepoData] Cleared: {path}")

############################################################
# Start Message

os.chdir(packwiz_path)

with open(packwiz_manifest, "r") as f:
    pack_toml = toml.load(f)
pack_version = pack_toml["version"]
modpack_name = pack_toml["name"]
minecraft_version = pack_toml["versions"]["minecraft"]
active_mod_loader, mod_loader_version = get_pack_mod_loader_details(pack_toml)

input(f"""{launch_message}
Modpack: {modpack_name}
Version: {pack_version}
Minecraft: {minecraft_version}
Modloader: {get_mod_loader_label(active_mod_loader)} {mod_loader_version}

Press Enter to continue...""")

############################################################
# Configuration

@dataclass
class Settings:
    # Boolean flags
    update_crash_assistant_modlist: bool = False
    export_client: bool = False
    export_server: bool = False
    refresh_only: bool = False
    update_bcc_version: bool = False
    cleanup_temp: bool = False
    create_release_notes: bool = False
    print_path_debug: bool = False
    update_publish_workflow: bool = False
    download_comparison_files: bool = False
    generate_mods_changelog: bool = False
    generate_primary_changelog: bool = False
    breakneck_fixes: bool = False
    github_auth: bool = False
    changelog_side_tag: bool = True
    changelog_updated_mods: bool = False
    changelog_updated_resoucepacks: bool = False
    modlist_side_tag: bool = True
    update_mods_only: bool = False
    bump_version_only: bool = False
    clear_repo_data_only: bool = False
    generate_update_summary_only: bool = False
    list_disabled_mods_only: bool = False
    add_mod_only: bool = False
    migrate_minecraft_version: bool = False
    migration_disable_incompatible_mods: bool = True
    migration_update_all_mods: bool = True
    auto_generate_update_overview: bool = False
    auto_summary_overwrite_existing: bool = False
    auto_generate_config_changes: bool = False
    auto_config_overwrite_existing: bool = False
    auto_config_include_removed_files: bool = True

    # String settings
    bh_banner: str = ""
    repo_owner: str = ""
    repo_name: str = ""
    repo_main_branch: str = ""
    migration_target_minecraft: str = ""
    migration_target_fabric: str = ""
    migration_target_mod_loader: str = ""
    migration_target_mod_loader_version: str = ""
    migration_mod_loader: str = "fabric"
    alpha_update_policy: str = "prompt"
    bump_target_version: str = ""
    auto_summary_provider: str = "ollama"
    auto_summary_model: str = "qwen3:4b-instruct"
    auto_summary_endpoint: str = "http://127.0.0.1:11434/api/generate"
    auto_config_provider: str = "ollama"
    auto_config_model: str = "qwen3:4b-instruct"
    auto_config_endpoint: str = "http://127.0.0.1:11434/api/generate"

    # List settings
    server_mods_remove_list: List[str] = None
    auto_summary_timeout_seconds: int = 45
    auto_summary_max_items: int = 8
    auto_config_timeout_seconds: int = 45
    auto_config_temperature: float = 0.25
    auto_config_max_items: int = 20
    auto_config_max_lines: int = 18


def update_settings_from_dict(settings: Settings, settings_dict: dict):
    for key, value in settings_dict.items():
        if hasattr(settings, key):
            setattr(settings, key, value)
        else:
            print(f"Warning: '{key}' is not a valid setting attribute.")


# Load settings.yml with ruamel (instead of yaml.safe_load)
with open(settings_path, "r", encoding="utf-8") as s_file:
    settings_yml = yaml.load(s_file) or {}

settings = Settings()
update_settings_from_dict(settings, settings_yml)

############################################################
# Print Stuff

if settings.print_path_debug:
    print("[DEBUG] " + git_path)
    print("[DEBUG] " + packwiz_path)
    print("[DEBUG] " + packwiz_exe_path)
    print("[DEBUG] " + bcc_client_config_path)
    print("[DEBUG] " + bcc_server_config_path)

############################################################
# Class Objects

changelog_factory = ChangelogFactory(changelog_dir_path, modpack_name, pack_version, settings, yaml)

############################################################
# Main Program

def has_special_menu_action_selected(settings):
    return any(
        [
            settings.refresh_only,
            settings.update_mods_only,
            settings.bump_version_only,
            settings.clear_repo_data_only,
            settings.generate_update_summary_only,
            settings.list_disabled_mods_only,
            settings.add_mod_only,
        ]
    )


def run_special_menu_action(settings):
    if settings.clear_repo_data_only:
        clear_stored_repository_data()
    elif settings.bump_version_only:
        bump_modpack_version(settings.bump_target_version)
        subprocess.call(f"{packwiz_exe_path} refresh", shell=True)
    elif settings.generate_update_summary_only:
        download_missing_comparison_files()
        run_changelog_auto_generation()
    elif settings.list_disabled_mods_only:
        list_disabled_mods()
    elif settings.add_mod_only:
        add_mod_via_prompt()
    elif settings.update_mods_only:
        previous_snapshot = snapshot_mod_toml_content()
        subprocess.call(f"{packwiz_exe_path} refresh", shell=True)

        pinned_updates = find_pinned_mods_with_available_updates()
        temp_unpinned_mod_files = []
        allowed_alpha_mod_files = []
        if pinned_updates:
            selected_pinned_mod_files, allowed_alpha_mod_files = prompt_for_pinned_mod_updates(pinned_updates)
            if selected_pinned_mod_files:
                temp_unpinned_mod_files = set_pin_state_for_mod_files(selected_pinned_mod_files, should_pin=False)
                if temp_unpinned_mod_files:
                    subprocess.call(f"{packwiz_exe_path} refresh", shell=True)

        try:
            subprocess.call(f"{packwiz_exe_path} update --all -y", shell=True)
        finally:
            if temp_unpinned_mod_files:
                repinned_mod_files = set_pin_state_for_mod_files(temp_unpinned_mod_files, should_pin=True)
                if repinned_mod_files:
                    subprocess.call(f"{packwiz_exe_path} refresh", shell=True)
                    print(f"[PackWiz] Re-pinned {len(repinned_mod_files)} pinned mods after update.")

        enforce_release_channel_policy(
            previous_snapshot,
            log_prefix="[Update]",
            allowed_alpha_mod_files=allowed_alpha_mod_files,
        )
        subprocess.call(f"{packwiz_exe_path} refresh", shell=True)
        print("[PackWiz] Mods updated.")

        updated_disabled_mods = find_updated_disabled_mods(previous_snapshot)
        if updated_disabled_mods:
            updated_disabled_names = [mod_name for _, mod_name in updated_disabled_mods]
            print("[PackWiz] Updated mods that are still disabled: " + ", ".join(updated_disabled_names))
            if input("Enable these updated disabled mods? [N]: ") in ("y", "Y", "yes", "Yes"):
                enabled_mods = enable_mods_by_files([mod_file for mod_file, _ in updated_disabled_mods])
                subprocess.call(f"{packwiz_exe_path} refresh", shell=True)
                print(f"[PackWiz] Re-enabled {len(enabled_mods)} updated disabled mods.")
    elif settings.refresh_only:
        subprocess.call(f"{packwiz_exe_path} refresh", shell=True)


def run_release_notes_generation(settings):
    os.chdir(git_path)
    changelog_path = os.path.join(git_path, "Changelogs", f"{pack_version}+{minecraft_version}.yml")
    major_minecraft_version = '.'.join(minecraft_version.split('.', 2)[:2])
    md_element_full_changelog = f"**[[Full Changelog]](https://crismpack.net/{modpack_name.lower().split(' ', 1)[0]}/changelogs/{major_minecraft_version}/{minecraft_version}#v{pack_version})**"
    md_element_pre_release = '**This is a pre-release. Here be dragons!**'
    md_element_bh_banner = f"[![BisectHosting Banner]({settings.bh_banner})](https://bisecthosting.com/CRISM)"
    mdFile_CF = MdUtils(file_name='CurseForge-Release')
    mdFile_MR = MdUtils(file_name='Modrinth-Release')

    if "beta" in pack_version or "alpha" in pack_version:
        print("pack_version = " + pack_version)
        mdFile_CF.new_paragraph(md_element_pre_release)
        mdFile_MR.new_paragraph(md_element_pre_release)

    if not os.path.isfile(changelog_path):
        print(f"No changelog found for {pack_version}, creating a template...")

        data = CommentedMap()
        data["version"] = pack_version
        data["Mod loader"] = get_mod_loader_label(active_mod_loader)
        data["Mod loader version"] = mod_loader_version
        if normalize_mod_loader_name(active_mod_loader) == "fabric":
            data["Fabric version"] = mod_loader_version
        data["Changes/Improvements"] = None
        data["Bug Fixes"] = None
        data["Config Changes"] = LiteralScalarString("- : [mod], [Client]")

        with open(changelog_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

    with open(changelog_path, "r", encoding="utf-8") as f:
        changelog_yml = yaml.load(f) or {}

    if apply_loader_metadata_to_changelog(changelog_yml, active_mod_loader, mod_loader_version):
        with open(changelog_path, "w", encoding="utf-8") as f:
            yaml.dump(changelog_yml, f)

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
    if settings.export_client:
        os.chdir(packwiz_path)
        with open(bcc_client_config_path, "r") as f:
            bcc_json = json.load(f)
        bcc_json["modpackVersion"] = pack_version
        with open(bcc_client_config_path, "w") as f:
            json.dump(bcc_json, f)
    if settings.export_server:
        with open(bcc_server_config_path, "r") as f:
            bcc_json = json.load(f)
        bcc_json["modpackVersion"] = pack_version
        with open(bcc_server_config_path, "w") as f:
            json.dump(bcc_json, f)


def update_crash_assistant_modlist(settings):
    mod_filenames_json = parse_filenames_as_json(mods_path)
    with open(crash_assistant_config_path, "w", encoding="utf8") as output_file:
        output_file.write(mod_filenames_json)
    combined_modlist_markdown = build_combined_modlist_markdown(
        mods_path,
        include_side_tags=settings.modlist_side_tag
    )
    with open(crash_assistant_markdown_path, "w", encoding="utf8") as output_file:
        output_file.write(combined_modlist_markdown)


def main():
    global minecraft_version, active_mod_loader, mod_loader_version

    special_menu_action_selected = has_special_menu_action_selected(settings)

    if not special_menu_action_selected:
        if settings.breakneck_fixes and (settings.export_client or settings.export_server):
            input("Using fixes for Breakneck. Press Enter to continue...")

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

        #----------------------------------------
        # Download comparison files.
        #----------------------------------------
        download_missing_comparison_files()

        #----------------------------------------
        # Auto-generate changelog update overview.
        #----------------------------------------
        if settings.auto_generate_update_overview or settings.auto_generate_config_changes:
            run_changelog_auto_generation()

        #----------------------------------------
        # Generate CHANGELOG.md file.
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
        # Export client pack. (CurseForge with Packwiz)
        #----------------------------------------
        os.chdir(packwiz_path)
        subprocess.call(f"{packwiz_exe_path} refresh", shell=True)

        client_zip_name = f'{modpack_name}-{pack_version}.zip'
        if settings.export_client and not settings.breakneck_fixes:
            subprocess.call(f"{packwiz_exe_path} cf export", shell=True)
            move(client_zip_name, os.path.join(export_path, client_zip_name))
            print("[PackWiz] Client exported.")

        #----------------------------------------
        # Export client pack. (CurseForge & Modrinth with MMC) — Breakneck only
        #----------------------------------------
        if settings.export_client and settings.breakneck_fixes:

            bootstrap_nogui = False
            mmc_cache_path = os.path.join(packwiz_path, "mmc-cache")
            mmc_dotminecraft_path = os.path.join(mmc_cache_path, ".minecraft")
            mmc_input_path = os.path.join(packwiz_path, "mcc-cache.zip")
            packwiz_installer_path = os.path.join(git_path, "Modpack-CLI-Tool", "packwiz-installer-bootstrap.jar")
            mmc_config = os.path.join(packwiz_path, "mmc-export.toml")

            packwiz_side = "client"
            export_mmc_modrinth = True
            export_mmc_curseforge = True
            cleanup_cache = True
            move_disabled_mods = True

            os.chdir(packwiz_path)

            if move_disabled_mods:
                disabled_mods_path = os.path.join(mods_path, "disabled")
                os.makedirs(disabled_mods_path, exist_ok=True)
                os.chdir(mods_path)
                for item in os.listdir():
                    if os.path.isdir(item) and item == "disabled":
                        continue
                    if not item.endswith(".toml"):
                        continue
                    try:
                        with open(item, "r") as f:
                            mod_toml = toml.load(f)
                        if "disabled" in str(mod_toml.get("side", "")).lower():
                            move(item, disabled_mods_path)
                    except OSError as e:
                        print(f"move_disabled_mods: {e}")
                os.chdir(packwiz_path)
                subprocess.call(f"{packwiz_exe_path} refresh", shell=True)

            # Ensure mmc-cache exists and is clean
            try:
                os.mkdir(mmc_cache_path)
            except FileExistsError:
                pass
            clear_mmc_cache(mmc_cache_path)

            # FIX: proper path join when checking for installer in cache
            installer_cached = Path(os.path.join(mmc_cache_path, "packwiz-installer.jar"))

            # Run bootstrap to generate the mmc cache
            if bootstrap_nogui:
                if installer_cached.is_file():
                    subprocess.call(f"java -jar \"{packwiz_installer_path}\" -s {packwiz_side} \"{os.path.join(packwiz_path, packwiz_manifest)}\" -g --bootstrap-no-update", shell=True)
                else:
                    subprocess.call(f"java -jar \"{packwiz_installer_path}\" -s {packwiz_side} \"{os.path.join(packwiz_path, packwiz_manifest)}\" -g", shell=True)
            else:
                if installer_cached.is_file():
                    subprocess.call(f"java -jar \"{packwiz_installer_path}\" -s {packwiz_side} \"{os.path.join(packwiz_path, packwiz_manifest)}\" --bootstrap-no-update", shell=True)
                else:
                    subprocess.call(f"java -jar \"{packwiz_installer_path}\" -s {packwiz_side} \"{os.path.join(packwiz_path, packwiz_manifest)}\"", shell=True)

            # Ensure .minecraft exists
            try:
                os.mkdir(mmc_dotminecraft_path)
            except FileExistsError:
                pass

            # Move override folders into .minecraft
            move_list = ["shaderpacks", "resourcepacks", "mods", "config"]
            for item in os.listdir(os.getcwd()):
                if item in move_list:
                    move(item, mmc_dotminecraft_path)

            if move_disabled_mods:
                os.chdir(disabled_mods_path)
                retain = [".gitkeep"]
                try:
                    for item in os.listdir():
                        if item not in retain:
                            move(item, mods_path)
                except OSError as e:
                    print(e)
                os.chdir(packwiz_path)

            # Create the input zip from mmc-cache
            make_archive("mcc-cache", 'zip', mmc_cache_path)  # produces Packwiz/mcc-cache.zip
            mmc_input_path = os.path.join(packwiz_path, "mcc-cache.zip")

            # Sanity check: zip must contain instance.cfg for mmc_export
            import zipfile
            with zipfile.ZipFile(mmc_input_path) as zf:
                if not any(p.endswith("instance.cfg") for p in zf.namelist()):
                    raise RuntimeError(
                        "mcc-cache.zip is missing instance.cfg. The Packwiz bootstrap step likely failed (Java not installed or jar path wrong)."
                    )

            # Export Modrinth using mmc_export
            if export_mmc_modrinth:
                print("[MMC] Exporting Modrinth...")
                cmd = [
                    sys.executable, "-m", "mmc_export",
                    "--input", mmc_input_path,
                    "--format", "Modrinth",
                    "--modrinth-search", "loose",
                    "-o", export_path,
                    "-c", mmc_config,
                    "-v", pack_version,
                    "--scheme", f"{modpack_name}-{minecraft_version}-{{version}}",
                ]
                subprocess.run(cmd, check=True)
                print("[MMC] Modrinth exported.")

            # Export CurseForge using mmc_export (consistent invocation)
            if export_mmc_curseforge:
                print("[MMC] Exporting CurseForge...")
                cmd_cf = [
                    sys.executable, "-m", "mmc_export",
                    "--input", mmc_input_path,
                    "--format", "CurseForge",
                    "-o", export_path,
                    "-c", mmc_config,
                    "-v", pack_version,
                    "--scheme", f"{modpack_name}-{minecraft_version}-{{version}}",
                ]
                subprocess.run(cmd_cf, check=True)
                print("[MMC] CurseForge exported.")

            if cleanup_cache:
                os.remove("mcc-cache.zip")
                clear_mmc_cache(mmc_cache_path)
                print("Cache cleanup finished.")

            os.chdir(packwiz_path)
            subprocess.call(f"{packwiz_exe_path} refresh", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        #----------------------------------------
        # Export server pack
        # ----------------------------------------
        if settings.export_server:
            # Export CF modpack using Packwiz (server side)
            existing_server_archives = {
                archive.name: archive.stat().st_mtime
                for archive in Path(packwiz_path).glob("*.zip")
            }
            server_export_code = subprocess.call(f"{packwiz_exe_path} cf export -s server", shell=True)
            if server_export_code != 0:
                raise RuntimeError(f"Packwiz server export failed with exit code {server_export_code}.")

            expected_server_zip_name = f"{modpack_name}-Server-{pack_version}.zip"
            expected_server_zip_path = Path(packwiz_path) / expected_server_zip_name
            changed_archives = [
                archive for archive in Path(packwiz_path).glob("*.zip")
                if archive.name not in existing_server_archives
                or archive.stat().st_mtime > existing_server_archives[archive.name]
            ]

            if expected_server_zip_path.exists():
                server_zip_name = expected_server_zip_name
            elif changed_archives:
                server_zip_name = max(changed_archives, key=lambda archive: archive.stat().st_mtime).name
            else:
                raise FileNotFoundError(
                    "Could not find the server export zip produced by Packwiz. "
                    "Check Packwiz output for the generated filename."
                )

            move(server_zip_name, os.path.join(export_path, server_zip_name))
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

        #----------------------------------------
        # Temp cleanup
        #----------------------------------------
        if settings.cleanup_temp and os.path.isdir(tempfolder_path):
            rmtree(tempfolder_path)
            print("Temp folder cleanup finished.")

        os.chdir(packwiz_path)
        subprocess.call(f"{packwiz_exe_path} refresh", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    else:
        run_special_menu_action(settings)


if __name__ == "__main__":
    try:
        while True:
            if not configure_actions_via_menu(settings):
                print("No action selected. Exiting.")
                break
            print("")
            main()
            input("\nWorkflow complete. Press Enter to return to the menu...")
    except KeyboardInterrupt:
        print("Operation aborted by user.")
        exit(-1)
