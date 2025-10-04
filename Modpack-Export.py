launch_message = """
▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
█                           █
█  HaXr's Modpack CLI Tool  █
█                           █
▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀"""

import os, sys
import os.path
import json
import subprocess
from shutil import rmtree, make_archive, move, copytree
from pathlib import Path

import toml  # pip install toml
import yaml # pip install PyYAML
from ruamel.yaml import YAML
from mdutils.mdutils import MdUtils
from mdutils import Html
import re
import requests
# from packaging import version as version_helper

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

############################################################
# Start Message

os.chdir(packwiz_path)

with open(packwiz_manifest, "r") as f:
    pack_toml = toml.load(f)
pack_version = pack_toml["version"]
modpack_name = pack_toml["name"]
minecraft_version = pack_toml["versions"]["minecraft"]

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

    # String settings
    bh_banner: str = ""
    repo_owner: str = ""
    repo_name: str = ""
    repo_main_branch: str = ""

    # List settings
    server_mods_remove_list: List[str] = None


def update_settings_from_dict(settings: Settings, settings_dict: dict):
    for key, value in settings_dict.items():
        if hasattr(settings, key):
            setattr(settings, key, value)
        else:
            print(f"Warning: '{key}' is not a valid setting attribute.")


with open(settings_path, "r") as s_file:
    settings_yml = yaml.safe_load(s_file)

settings = Settings()
update_settings_from_dict(settings, settings_yml)

settings.export_server = determine_server_export()

############################################################
# Print Stuff

if settings.breakneck_fixes:
    input("Using fixes for Breakneck. Press Enter to continue...")

if settings.print_path_debug:
    print("[DEBUG] " + git_path)
    print("[DEBUG] " + packwiz_path)
    print("[DEBUG] " + packwiz_exe_path)
    print("[DEBUG] " + bcc_client_config_path)
    print("[DEBUG] " + bcc_server_config_path)

############################################################
# Class Objects

changelog_factory = ChangelogFactory(changelog_dir_path, modpack_name, pack_version, settings)

############################################################
# Main Program

def main():

    if not settings.refresh_only:

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
            changelog_factory.build_markdown_changelog(settings.repo_owner, settings.repo_name, tempgit_path, packwiz_path, repo_branch = settings.repo_main_branch, mc_version=minecraft_version)

        #----------------------------------------
        # Update publish workflow values.
        #----------------------------------------
        if settings.update_publish_workflow:
            os.chdir(git_path)
            yaml2 = YAML()
            publish_workflow_path = os.path.join(git_path, ".github", "workflows", "publish.yml")
            with open(publish_workflow_path, "r") as pw_file:
                publish_workflow_yml = yaml2.load(pw_file)
            publish_workflow_yml['env']['MC_VERSION'] = minecraft_version
            if "beta" in pack_version:
                pw_release_type = "beta"; pw_prerelease = True
            elif "alpha" in pack_version:
                pw_release_type = "alpha"; pw_prerelease = True
            else:
                pw_release_type = "release"; pw_prerelease = False
            publish_workflow_yml['env']['RELEASE_TYPE'] = pw_release_type
            publish_workflow_yml['env']['PRE_RELEASE'] = pw_prerelease
            with open(publish_workflow_path, "w") as pw_file:
                yaml2.dump(publish_workflow_yml, pw_file)

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
            with open(changelog_path, "r", encoding="utf8") as f:
                changelog_yml = yaml.safe_load(f)
            try:
                update_overview = changelog_yml['Update overview']
                mdFile_CF.new_paragraph(markdown.markdown_list_maker(update_overview))
                mdFile_MR.new_paragraph(markdown.markdown_list_maker(update_overview))
            except:
                try:
                    improvements = changelog_yml['Changes/Improvements']
                    bug_fixes = changelog_yml['Bug Fixes']
                    if improvements:
                        mdFile_CF.new_paragraph("### Changes/Improvements ⭐")
                        mdFile_CF.new_paragraph(markdown.markdown_list_maker(improvements))
                        mdFile_MR.new_paragraph(markdown.markdown_list_maker(improvements))
                    if bug_fixes:
                        mdFile_CF.new_paragraph("### Bug Fixes 🪲")
                        mdFile_CF.new_paragraph(markdown.markdown_list_maker(bug_fixes))
                        mdFile_MR.new_paragraph(markdown.markdown_list_maker(bug_fixes))
                except:
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
        subprocess.call(f"{packwiz_exe_path} refresh", shell=True)


if __name__ == "__main__":
    try:
        print("")
        main()
    except KeyboardInterrupt:
        print("Operation aborted by user.")
        exit(-1)