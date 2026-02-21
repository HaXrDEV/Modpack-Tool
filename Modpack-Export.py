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
from typing import List

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
modrinth_api_base = "https://api.modrinth.com/v2"

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


def configure_actions_via_menu(settings):
    print(
        """
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
0) Exit
"""
    )

    choice = input("Selection [1]: ").strip() or "1"

    if choice == "0":
        return False

    # Reset runtime flow toggles before applying chosen mode.
    settings.refresh_only = False
    settings.update_mods_only = False
    settings.bump_version_only = False
    settings.migrate_minecraft_version = False
    settings.export_client = False
    settings.export_server = False

    if choice == "1":
        # Keep the configured export_client value while preserving existing server prompt behavior.
        with open(settings_path, "r", encoding="utf-8") as s_file:
            settings_yml_local = yaml.load(s_file) or {}
        settings.export_client = bool(settings_yml_local.get("export_client", False))
        settings.export_server = determine_server_export()
        return True

    if choice == "2":
        settings.migrate_minecraft_version = True
        ensure_migration_targets(settings)
        return True

    if choice == "3":
        settings.export_client = True
        return True

    if choice == "4":
        settings.export_server = True
        return True

    if choice == "5":
        settings.migrate_minecraft_version = True
        settings.export_client = True
        ensure_migration_targets(settings)
        return True

    if choice == "6":
        settings.migrate_minecraft_version = True
        settings.export_client = True
        settings.export_server = True
        ensure_migration_targets(settings)
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

    print(f"Unknown choice '{choice}'. Falling back to configured workflow.")
    with open(settings_path, "r", encoding="utf-8") as s_file:
        settings_yml_local = yaml.load(s_file) or {}
    settings.export_client = bool(settings_yml_local.get("export_client", False))
    settings.export_server = determine_server_export()
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
        subprocess.call(f"{packwiz_exe_path} update --all -y", shell=True)
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
                "- Updated mods and resource packs.\n"
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
    update_mods_only: bool = False
    bump_version_only: bool = False
    migrate_minecraft_version: bool = False
    migration_disable_incompatible_mods: bool = True
    migration_update_all_mods: bool = True

    # String settings
    bh_banner: str = ""
    repo_owner: str = ""
    repo_name: str = ""
    repo_main_branch: str = ""
    migration_target_minecraft: str = ""
    migration_target_fabric: str = ""
    migration_mod_loader: str = "fabric"
    bump_target_version: str = ""

    # List settings
    server_mods_remove_list: List[str] = None


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
        if settings.download_comparison_files:
            # Handle GitHub authentication
            if settings.github_auth:
                github_token = input("Your personal access token: ")
            else:
                github_token = None

            async def download_compare_files_async(input_version, destination):
                print(f"Downloading {input_version} comparison files.")
                if settings.breakneck_fixes and (
                    is_version_in_range(input_version, "4.0.0-beta.3", "4.4.0-beta.1")
                ):
                    tag_mc_ver = changelog_factory.get_changelog_value(changelog, "mc_version")
                    packwiz_mods_folder = f'Packwiz/{tag_mc_ver}/mods'
                    packwiz_resourcepacks_folder = f'Packwiz/{tag_mc_ver}/resourcepacks'
                else:
                    packwiz_mods_folder = 'Packwiz/mods'
                    packwiz_resourcepacks_folder = 'Packwiz/resourcepacks'

                local_downloader = AsyncGitHubDownloader(settings.repo_owner, settings.repo_name, token=github_token, branch=input_version)
                await local_downloader.download_folder(packwiz_mods_folder, os.path.join(destination, "mods"))
                await local_downloader.download_folder(packwiz_resourcepacks_folder, os.path.join(destination, "resourcepacks"))
                return

            for changelog in reversed(os.listdir(changelog_dir_path)):
                if changelog.endswith(('.yml', '.yaml')):
                    version = str(changelog_factory.get_changelog_value(changelog, "version"))
                    version_path = os.path.join(tempgit_path, version)
                    if version != pack_version and not os.path.exists(version_path):
                        os.makedirs(version_path)
                        try:
                            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
                            asyncio.run(download_compare_files_async(version, version_path))
                        except Exception as ex:
                            print(ex)

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
        if settings.bump_version_only:
            bump_modpack_version(settings.bump_target_version)
            subprocess.call(f"{packwiz_exe_path} refresh", shell=True)
        elif settings.update_mods_only:
            previous_snapshot = snapshot_mod_toml_content()
            subprocess.call(f"{packwiz_exe_path} refresh", shell=True)
            subprocess.call(f"{packwiz_exe_path} update --all -y", shell=True)
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
