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
_mod_label_index_cache = None

############################################################
# Functions

def determine_server_export():
    """Determine whether the server pack should be exported or not and return a boolean."""
    if settings.export_server:
        if input("Want to export server pack? [N]: ") in ("y", "Y", "yes", "Yes"):
            return True
        else:
            return False
    else:
        return False


def ensure_migration_targets(settings):
    if not settings.migration_target_minecraft:
        settings.migration_target_minecraft = input("Target Minecraft version for migration: ").strip()
    if not settings.migration_target_minecraft:
        raise ValueError("Migration selected but no target Minecraft version was provided.")

    prompt = f"Target Fabric version [{settings.migration_target_fabric}]: "
    target_fabric = input(prompt).strip()
    if target_fabric:
        settings.migration_target_fabric = target_fabric


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
        settings.refresh_only = True
        settings.update_mods_only = True
        return True

    if choice == "9":
        settings.refresh_only = True
        settings.bump_version_only = True
        target_version = input(f"New modpack version [{pack_version}]: ").strip()
        settings.bump_target_version = target_version if target_version else pack_version
        return True

    if choice == "10":
        settings.refresh_only = True
        settings.clear_repo_data_only = True
        return True

    if choice == "11":
        settings.refresh_only = True
        settings.generate_update_summary_only = True
        prompt_changelog_autogen_overwrite(force_prompt=True)
        return True

    if choice == "12":
        settings.refresh_only = True
        settings.list_disabled_mods_only = True
        return True

    if choice == "13":
        settings.refresh_only = True
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


def download_versioning_helper(local_version = str):
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
            except:
                pass
            try:
                rmtree(item)
            except:
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
        return [str(minecraft_version)], ["fabric"]

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


def infer_compatibility_from_metadata(mod_toml, target_minecraft_version):
    target_minor = ".".join(target_minecraft_version.split(".", 2)[:2])
    metadata = " ".join([
        str(mod_toml.get("filename", "")),
        str(mod_toml.get("download", {}).get("url", "")),
    ]).lower()
    if target_minecraft_version.lower() in metadata:
        return True
    explicit_mc_versions = re.findall(r"(?:mc|minecraft)[-_ +]?(\d+\.\d+(?:\.\d+)?)", metadata)
    if explicit_mc_versions:
        if target_minecraft_version in explicit_mc_versions:
            return True
        if target_minor in explicit_mc_versions:
            return True
        return False
    return True


def has_modrinth_version_for_target(mod_toml, target_minecraft_version, mod_loader):
    try:
        project_id = mod_toml.get("update", {}).get("modrinth", {}).get("mod-id")
        if not project_id:
            return None
        response = requests.get(
            f"{modrinth_api_base}/project/{project_id}/version",
            params={
                "loaders": json.dumps([mod_loader]),
                "game_versions": json.dumps([target_minecraft_version]),
            },
            timeout=20,
        )
        response.raise_for_status()
        return len(response.json()) > 0
    except Exception as ex:
        mod_name = mod_toml.get("name", "unknown mod")
        print(f"[Migration] Modrinth compatibility check failed for '{mod_name}': {ex}")
        return None


def disable_incompatible_mods(target_minecraft_version, mod_loader):
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
            if "disabled" in side_value:
                continue

            compatible = has_modrinth_version_for_target(mod_toml, target_minecraft_version, mod_loader)
            if compatible is None:
                compatible = infer_compatibility_from_metadata(mod_toml, target_minecraft_version)

            if compatible:
                continue

            mod_toml["side"] = normalize_disabled_side(side_value)
            with open(item_path, "w", encoding="utf8") as f:
                toml.dump(mod_toml, f)
            disabled_mods.append(mod_toml.get("name", item))
        except Exception as ex:
            print(f"[Migration] Failed to process '{item}': {ex}")

    return disabled_mods


def migrate_minecraft_version(
    target_minecraft_version,
    target_fabric_version=None,
    update_all_mods=True,
    disable_outdated_mods=True,
    mod_loader="fabric",
):
    os.chdir(packwiz_path)
    with open(packwiz_manifest, "r", encoding="utf8") as f:
        local_pack_toml = toml.load(f)

    current_minecraft = str(local_pack_toml["versions"]["minecraft"])
    current_fabric = str(local_pack_toml["versions"].get("fabric", ""))

    if not target_minecraft_version:
        print("[Migration] migration_target_minecraft is empty. Skipping migration.")
        return current_minecraft, current_fabric

    local_pack_toml["versions"]["minecraft"] = target_minecraft_version
    if target_fabric_version:
        local_pack_toml["versions"]["fabric"] = target_fabric_version

    with open(packwiz_manifest, "w", encoding="utf8") as f:
        toml.dump(local_pack_toml, f)

    subprocess.call(f"{packwiz_exe_path} refresh", shell=True)
    if update_all_mods:
        previous_snapshot = snapshot_mod_toml_content()
        subprocess.call(f"{packwiz_exe_path} update --all -y", shell=True)
        enforce_release_channel_policy(previous_snapshot, log_prefix="[Migration]")
        subprocess.call(f"{packwiz_exe_path} refresh", shell=True)

    disabled_mods = []
    if disable_outdated_mods:
        disabled_mods = disable_incompatible_mods(target_minecraft_version, mod_loader)
        subprocess.call(f"{packwiz_exe_path} refresh", shell=True)

    print(
        f"[Migration] Minecraft {current_minecraft} -> {target_minecraft_version}"
        + (f", Fabric -> {target_fabric_version}" if target_fabric_version else "")
    )
    print(f"[Migration] Disabled {len(disabled_mods)} incompatible mods.")
    if disabled_mods:
        print("[Migration] Disabled mods: " + ", ".join(disabled_mods))

    return (
        target_minecraft_version,
        target_fabric_version if target_fabric_version else current_fabric,
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
    ensure_changelog_yml(pack_version, minecraft_version, fabric_version)
    changelog_factory = ChangelogFactory(changelog_dir_path, modpack_name, pack_version, settings, yaml)
    print(f"[Version] Modpack version bumped: {old_pack_version} -> {new_pack_version}")


def ensure_changelog_yml(target_pack_version, target_minecraft_version, target_fabric_version):
    os.makedirs(changelog_dir_path, exist_ok=True)
    changelog_path = os.path.join(changelog_dir_path, f"{target_pack_version}+{target_minecraft_version}.yml")

    if not os.path.isfile(changelog_path):
        if settings.breakneck_fixes:
            breakneck_template = (
                f"version: {target_pack_version}\n"
                f"mc_version: {target_minecraft_version}\n"
                "\n"
                f"Fabric version: {target_fabric_version}\n"
                "Update overview:\n"
                "Config Changes: |\n"
            )
            with open(changelog_path, "w", encoding="utf-8") as f:
                f.write(breakneck_template)
        else:
            data = CommentedMap()
            data["version"] = target_pack_version
            data["Fabric version"] = target_fabric_version
            data["Changes/Improvements"] = None
            data["Bug Fixes"] = None
            data["Config Changes"] = LiteralScalarString("- : [mod], [Client]")
            with open(changelog_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f)
        print(f"[Version] Created changelog template: {changelog_path}")
        return changelog_path

    with open(changelog_path, "r", encoding="utf-8") as f:
        changelog_yml = yaml.load(f) or {}
    if changelog_yml.get("Fabric version") != target_fabric_version:
        changelog_yml["Fabric version"] = target_fabric_version
        with open(changelog_path, "w", encoding="utf-8") as f:
            yaml.dump(changelog_yml, f)
        print(f"[Version] Updated Fabric version in changelog: {changelog_path}")
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
            normalized = line.rstrip(".").strip()
            lines.append(f"- {normalized}")
    return lines[:max_lines]


def _normalize_config_llm_bullets(raw_text: str, max_lines: int):
    normalized = _normalize_llm_text_to_bullets(raw_text, max_lines=max(max_lines * 2, max_lines))
    valid = []
    invalid_count = 0
    required_suffix_pattern = re.compile(r":\s*\[[^\[\]]+\]\.?$")

    for bullet in normalized:
        line = str(bullet or "").strip()
        if required_suffix_pattern.search(line):
            valid.append(line)
            if len(valid) >= max_lines:
                break
        else:
            invalid_count += 1

    return valid[:max_lines], invalid_count


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


def derive_mod_display_label_from_config_path(path: str) -> str:
    raw_path = str(path or "").replace("\\", "/").strip("/")
    filename = derive_mod_label_from_config_path(raw_path)
    stem = os.path.splitext(filename)[0].strip()
    parts = [p for p in raw_path.split("/") if p]
    parent = parts[-2] if len(parts) > 1 else ""

    label_index = _load_modlist_label_index()
    index = label_index.get("index", {})
    entries = label_index.get("entries", [])

    candidate_keys = [
        _normalize_lookup_key(stem),
        _normalize_lookup_key(filename),
        _normalize_lookup_key(parent),
    ]
    for key in candidate_keys:
        if key and key in index:
            return index[key]

    # Loose fallback: match by containment on normalized keys.
    stem_key = _normalize_lookup_key(stem)
    if stem_key:
        best_name = None
        best_score = 0
        for mod_name in entries:
            alias_key = _normalize_lookup_key(mod_name)
            if not alias_key:
                continue
            if stem_key == alias_key:
                return mod_name
            if stem_key in alias_key or alias_key in stem_key:
                score = min(len(stem_key), len(alias_key))
                if score > best_score:
                    best_score = score
                    best_name = mod_name
        if best_name and best_score >= 6:
            return best_name

    return format_config_filename_as_title(filename)


def _build_config_label_maps(diff_payload):
    config_diff = diff_payload.get("config_differences") or {}
    line_diffs = list(config_diff.get("modified_line_diffs", []))
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
    suffix_pattern = re.compile(r":\s*\[([^\[\]]+)\]\.?$")

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
    suffix_pattern = re.compile(r":\s*\[([^\[\]]+)\]\.?$")
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


def build_config_label_title_prompt(text: str, diff_payload, max_items=20):
    config_diff = diff_payload.get("config_differences") or {}
    modified_line_diffs = list(config_diff.get("modified_line_diffs", []))

    mapping_lines = []
    for entry in modified_line_diffs[:max_items]:
        path = str(entry.get("path", "")).strip()
        if not path:
            continue
        filename = derive_mod_label_from_config_path(path)
        resolved_label = derive_mod_display_label_from_config_path(path)
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
            removed_lines = entry.get("removed_lines", [])[:8]
            added_lines = entry.get("added_lines", [])[:8]
            if removed_lines:
                rendered.append("Removed lines:")
                rendered.extend(
                    f"- {_format_line_with_section_context(file_path, line, section_index_cache)}"
                    for line in removed_lines
                )
            if added_lines:
                rendered.append("Added lines:")
                rendered.extend(
                    f"- {_format_line_with_section_context(file_path, line, section_index_cache)}"
                    for line in added_lines
                )
            rendered.append("")
        return "\n".join(rendered).strip()

    def _render_path_to_label(items):
        if not items:
            return "none"
        rendered = []
        for entry in items[:max_items]:
            path = str(entry.get("path", ""))
            label = derive_mod_display_label_from_config_path(path)
            rendered.append(f"- {path} => {label}")
        return "\n".join(rendered)

    def _render_yosbr_moves(items):
        if not items:
            return "none"
        rendered = []
        for entry in items[:max_items]:
            from_path = str(entry.get("from", "")).strip()
            to_path = str(entry.get("to", "")).strip()
            if not from_path and not to_path:
                continue
            moved_label = f"{from_path} -> {to_path}".strip()
            if entry.get("content_changed"):
                rendered.append(f"- {moved_label} (moved to yosbr and content changed)")
            else:
                rendered.append(f"- {moved_label} (moved to yosbr)")
        return "\n".join(rendered) if rendered else "none"

    return (
        "Write concise end-user facing config change bullets for a Minecraft modpack changelog.\n"
        "Output only bullet lines. Each line must start with '- '.\n"
        "Keep wording factual and short. No markdown headers and no counts.\n"
        "Summarize what values or options changed, based on the changed lines.\n"
        "Files under 'yosbr/' are defaults applied only on first launch.\n"
        "When summarizing those files, use 'Changed default ...' instead of 'Changed ...'.\n"
        "Do not treat regular config -> yosbr moves as removals.\n"
        "For added/removed list entries, explicitly mention the section path when provided in '(section: ...)' context.\n"
        "Do not write generic lines like 'updated config files'.\n"
        "Do not write about added/removed config files.\n"
        "Only summarize in-file setting/value changes from modified files.\n"
        "Each bullet must end with ': [Mod Name]'.\n"
        "Use the file-to-mod mapping below when possible.\n"
        "Preferred style examples:\n"
        "- Changed adsEnabled to \"false\": [Resourcify]\n"
        "- Changed button URL to use the new wiki format: [Simple Discord RPC]\n"
        "- Changed \"coloredText\" to false: [Breakneck Menu]\n\n"
        "YOSBR style example:\n"
        "- Changed default \"coloredText\" to false: [Breakneck Menu]\n\n"
        f"Current version: {diff_payload.get('current_version')}\n"
        f"Compared from: {diff_payload.get('previous_version')}\n"
        f"Minecraft: {diff_payload.get('mc_version')}\n\n"
        "File-to-mod mapping:\n"
        f"{_render_path_to_label(modified_line_diffs)}\n\n"
        "Regular config files moved to yosbr:\n"
        f"{_render_yosbr_moves(moved_to_yosbr)}\n\n"
        "Modified config line changes (old/new):\n"
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


def generate_config_changes_with_llm(diff_payload, settings) -> Optional[str]:
    if str(settings.auto_config_provider).lower() != "ollama":
        print(f"[Changelog] Unsupported auto_config_provider '{settings.auto_config_provider}'.")
        return None

    base_prompt = build_config_changes_prompt(
        diff_payload,
        max_items=settings.auto_config_max_items,
    )
    max_attempts = 5
    best_text = None
    best_missing_labels = None
    missing_labels_for_retry = []

    for attempt in range(max_attempts):
        num_predict = 260 + (attempt * 180)
        coverage_hint = ""
        if missing_labels_for_retry:
            missing_render = "\n".join(f"- {label}" for label in missing_labels_for_retry[:12])
            coverage_hint = (
                "\nCoverage retry requirement:\n"
                "Ensure at least one bullet for each missing label below.\n"
                "Keep exact label spelling inside [] for these:\n"
                f"{missing_render}\n"
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
                normalized = _normalize_config_change_labels("\n".join(bullet_lines), diff_payload)
                titled = format_config_change_labels_with_llm(normalized, diff_payload, settings)
                if titled:
                    llm_text = _apply_yosbr_default_wording(
                        _normalize_config_change_title_labels(titled, diff_payload),
                        diff_payload,
                    )
                else:
                    llm_text = _apply_yosbr_default_wording(
                        _normalize_config_change_title_labels(normalized, diff_payload),
                        diff_payload,
                    )

                max_lines = int(settings.auto_config_max_lines)
                llm_bullets = _normalize_llm_text_to_bullets(llm_text, max_lines=max_lines)
                missing_labels = _get_missing_config_labels_from_bullets(llm_bullets, diff_payload)
                if best_text is None or (best_missing_labels is not None and len(missing_labels) < len(best_missing_labels)):
                    best_text = "\n".join(llm_bullets)
                    best_missing_labels = list(missing_labels)

                if not missing_labels:
                    return "\n".join(llm_bullets)

                if attempt + 1 < max_attempts:
                    print("[Changelog] LLM config output missed some config labels. Retrying...")
                    missing_labels_for_retry = list(missing_labels)
                    continue

                return best_text

            needs_retry = (
                attempt + 1 < max_attempts
                and (invalid_count > 0 or done_reason in ("length", "max_tokens"))
            )
            if needs_retry:
                print("[Changelog] LLM config generation returned incomplete output. Retrying...")
        except Exception as ex:
            print(f"[Changelog] LLM config change generation failed: {ex}")
            break

    return best_text


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
    moved_to_yosbr_bullets = generate_yosbr_default_move_bullets(
        diff_payload,
        max_lines=settings.auto_config_max_lines,
    )
    remaining_after_yosbr = max(int(settings.auto_config_max_lines) - len(moved_to_yosbr_bullets), 0)
    removed_config_file_bullets = generate_removed_config_file_bullets(
        diff_payload,
        max_lines=remaining_after_yosbr,
    )

    with open(changelog_path, "r", encoding="utf-8") as f:
        changelog_yml = yaml.load(f) or {}

    existing_config_changes = changelog_yml.get("Config Changes")
    existing_config_text = str(existing_config_changes or "").strip()
    is_default_placeholder = existing_config_text in ("- : [mod], [Client]", "")
    if existing_config_changes and not is_default_placeholder and not settings.auto_config_overwrite_existing:
        print("[Changelog] 'Config Changes' already exists. Skipping auto generation.")
        return

    if not modified_line_diffs and not removed_config_file_bullets and not moved_to_yosbr_bullets:
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

    if modified_line_diffs and remaining_line_budget > 0:
        if uses_llm_config_changes(settings):
            line_change_text = generate_config_changes_with_llm(diff_payload, settings)
            if line_change_text:
                source_parts.append("LLM")
            else:
                print("[Changelog] LLM output was empty or failed after retries. Skipping deterministic fallback.")

        if not line_change_text and not uses_llm_config_changes(settings):
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
            packwiz_mods_folder = f"Packwiz/{tag_mc_ver}/mods"
            packwiz_resourcepacks_folder = f"Packwiz/{tag_mc_ver}/resourcepacks"
            packwiz_shaderpacks_folder = f"Packwiz/{tag_mc_ver}/shaderpacks"
            packwiz_config_folder = f"Packwiz/{tag_mc_ver}/config"
        else:
            packwiz_mods_folder = "Packwiz/mods"
            packwiz_resourcepacks_folder = "Packwiz/resourcepacks"
            packwiz_shaderpacks_folder = "Packwiz/shaderpacks"
            packwiz_config_folder = "Packwiz/config"

        local_downloader = AsyncGitHubDownloader(
            settings.repo_owner,
            settings.repo_name,
            token=github_token,
            branch=input_version,
        )
        await local_downloader.download_folder(packwiz_mods_folder, os.path.join(destination, "mods"))
        await local_downloader.download_folder(packwiz_resourcepacks_folder, os.path.join(destination, "resourcepacks"))
        await local_downloader.download_folder(packwiz_shaderpacks_folder, os.path.join(destination, "shaderpacks"))
        await local_downloader.download_folder(
            packwiz_config_folder,
            os.path.join(destination, "config"),
            recursive=True,
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
        ensure_changelog_yml(pack_version, minecraft_version, fabric_version)

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
fabric_version = pack_toml["versions"]["fabric"]

input(f"""{launch_message}
Modpack: {modpack_name}
Version: {pack_version}
Minecraft: {minecraft_version}

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

    # String settings
    bh_banner: str = ""
    repo_owner: str = ""
    repo_name: str = ""
    repo_main_branch: str = ""
    migration_target_minecraft: str = ""
    migration_target_fabric: str = ""
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

def main():
    global minecraft_version, fabric_version

    if not settings.refresh_only:
        if settings.breakneck_fixes and (settings.export_client or settings.export_server):
            input("Using fixes for Breakneck. Press Enter to continue...")

        if settings.migrate_minecraft_version:
            minecraft_version, fabric_version = migrate_minecraft_version(
                target_minecraft_version=settings.migration_target_minecraft,
                target_fabric_version=settings.migration_target_fabric,
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

        #----------------------------------------
        # Create release notes.
        #----------------------------------------
        if settings.create_release_notes:
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
                data["Fabric version"] = fabric_version
                data["Changes/Improvements"] = None
                data["Bug Fixes"] = None
                data["Config Changes"] = LiteralScalarString("- : [mod], [Client]")

                with open(changelog_path, "w", encoding="utf-8") as f:
                    yaml.dump(data, f)

            with open(changelog_path, "r", encoding="utf-8") as f:
                changelog_yml = yaml.load(f) or {}

            # Update Fabric version in changelog if needed.
            if changelog_yml.get("Fabric version") != fabric_version:
                changelog_yml["Fabric version"] = fabric_version
                
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


        #----------------------------------------
        # Update BCC version number.
        #----------------------------------------
        if settings.update_bcc_version:
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

        #----------------------------------------
        # Update 'Crash Assistant' modlist.
        #----------------------------------------
        if settings.update_crash_assistant_modlist:
            mod_filenames_json = parse_filenames_as_json(mods_path)
            with open(crash_assistant_config_path, "w", encoding="utf8") as output_file:
                output_file.write(mod_filenames_json)
            combined_modlist_markdown = build_combined_modlist_markdown(
                mods_path,
                include_side_tags=settings.modlist_side_tag
            )
            with open(crash_assistant_markdown_path, "w", encoding="utf8") as output_file:
                output_file.write(combined_modlist_markdown)

        #----------------------------------------
        # Export client pack. (CurseForge with Packwiz)
        #----------------------------------------
        os.chdir(packwiz_path)
        subprocess.call(f"{packwiz_exe_path} refresh", shell=True)

        client_zip_name = f'{modpack_name}-{pack_version}.zip'
        if settings.export_client and settings.breakneck_fixes == False:
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
                os.chdir(mods_path)
                for item in os.listdir():
                    if os.path.isdir(item) and item == "disabled":
                        continue
                    try:
                        with open(item, "r") as f:
                            mod_toml = toml.load(f)
                            if "disabled" in mod_toml["side"]:
                                f.close()
                                move(item, disabled_mods_path)
                    except OSError as e:
                        print(f"move_disabled_mods: {e}")
                os.chdir(packwiz_path)
                subprocess.call(f"{packwiz_exe_path} refresh", shell=True)

            # Ensure mmc-cache exists and is clean
            try:
                os.mkdir(mmc_cache_path)
            except:
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
            except:
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
            subprocess.call(f"{packwiz_exe_path} cf export -s server", shell=True)
            server_zip_name = f'{modpack_name}-Server-{pack_version}.zip'
            move(server_zip_name, os.path.join(export_path, server_zip_name))
            print("[PackWiz] Server exported.")

            os.chdir(git_path)
            if os.path.isdir(tempfolder_path):
                rmtree(tempfolder_path)

            copytree("Server Pack", tempfolder_path)

            server_mods_path = input(f'Create a new modpack instance in the CurseForge launcher using the {server_zip_name} file. Then drag the mods folder from that instance into the terminal (No spaces allowed for the source directory): ')

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

    elif settings.refresh_only:
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
        else:
            subprocess.call(f"{packwiz_exe_path} refresh", shell=True)


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
