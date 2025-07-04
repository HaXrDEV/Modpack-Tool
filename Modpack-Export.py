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
    """This method determines whether whether the server pack should be exported or not and returns a boolean."""
    if settings.export_server:
        if input("Want to export server pack? [N]: ") in ("y", "Y", "yes", "Yes"):
            return True
        else:
            return False
    else:
        return False


def parse_active_projects(input_path, parse_object):
    """This method takes a path as input and parses the pw.toml files inside, returning the names of activate projects in a list."""
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
    """This method takes a path as input, parses the pw.toml files inside, 
    and returns the values of the 'filename' key as a JSON list."""
    filenames = []
    
    for mod_toml in os.listdir(input_path):
        mod_toml_path = os.path.join(input_path, mod_toml)
        try:
            if os.path.isfile(mod_toml_path):  # Checks if mod_toml_path is a file.
                with open(mod_toml_path, "r", encoding="utf8") as f:
                    mod_toml = toml.load(f)
                    side = str(mod_toml['side'])
                    
                    # Extract the 'filename' key if it exists
                    if 'filename' in mod_toml and side in ("both", "client", "server"):
                        filenames.append(mod_toml['filename'])
        except Exception as ex:
            print(f"Error processing file {mod_toml}: {ex}")

    # Sort the filenames alphabetically
    filenames.sort(key=lambda x: x.lower())
    
    # Convert the list of filenames to JSON and return it
    return json.dumps(filenames, indent=2)


def make_and_delete_dir(dir):
    """This function takes a directory path as a string and either clears its content if it already exists, or creates it if it doesn't."""
    if os.path.exists(dir):
        rmtree(dir)
        os.makedirs(dir)
    else:
        os.makedirs(dir)


def get_latest_release_version(owner, repo):
    """
    Retrieve the latest release version from a GitHub repository.

    Parameters:
    - owner (str): The owner of the GitHub repository (e.g., 'torvalds' for https://github.com/torvalds/linux).
    - repo (str): The name of the GitHub repository (e.g., 'linux' for https://github.com/torvalds/linux).

    Returns:
    - str: The tag name of the latest release version, or a message if no release found.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    headers = {"Accept": "application/vnd.github.v3+json"}

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes

        data = response.json()

        # Return the tag name of the latest release
        return data.get("tag_name", "No releases found.")
    
    except requests.exceptions.HTTPError as http_err:
        return f"HTTP error occurred: {http_err}"
    except Exception as err:
        return f"Error occurred: {err}"


# Unused
def download_versioning_helper(local_version = str):
    if "alpha" in local_version or "beta" in local_version:
        return local_version.replace("-", "_")
    else:
        return local_version + "+"
    


def is_version_in_range(input_version, min_version=None, max_version=None, include_min=True, include_max=True):
    """
    Compare semantic versions.

    :param input_version: The input version as a string (e.g., "4.1.3").
    :param min_version: The minimum version as a string (inclusive or exclusive based on include_min).
    :param max_version: The maximum version as a string (inclusive or exclusive based on include_max).
    :param include_min: Whether the minimum version is inclusive (default: True).
    :param include_max: Whether the maximum version is inclusive (default: True).
    :return: True if input_version is in range, False otherwise.
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


def clear_mmc_cache(path):
    os.chdir(path)
    retain = ["packwiz-installer.jar"] # Files that shouldn't be deleted
    
    # Loop through everything in folder in current working directory
    for item in os.listdir(os.getcwd()):
        if item not in retain:  # If it isn't in the list for retaining
            try:
                os.remove(item)  # Remove the item
            except:
                pass
            try:
                rmtree(item)
            except:
                pass


############################################################
# Start Message

os.chdir(packwiz_path)

# Parse pack.toml for modpack version.
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

            # Function to download comparison files asynchronously
            async def download_compare_files_async(input_version, destination):
                print(f"Downloading {input_version} comparison files.")

                # A fix that ensures that the mods folder is correctly targeted in versions that use a monorepo in Breakneck.
                if settings.breakneck_fixes and (
                    is_version_in_range(input_version, "4.0.0-beta.3", "4.4.0-beta.1")
                ):
                    tag_mc_ver = changelog_factory.get_changelog_value(changelog, "mc_version")
                    packwiz_mods_folder = f'Packwiz/{tag_mc_ver}/mods'
                    packwiz_resourcepacks_folder = f'Packwiz/{tag_mc_ver}/resourcepacks'
                else:
                    packwiz_mods_folder = 'Packwiz/mods'
                    packwiz_resourcepacks_folder = 'Packwiz/resourcepacks'

                # Download the folder from GitHub
                local_downloader = AsyncGitHubDownloader(settings.repo_owner, settings.repo_name, token=github_token, branch=input_version)
                await local_downloader.download_folder(packwiz_mods_folder, os.path.join(destination, "mods"))
                await local_downloader.download_folder(packwiz_resourcepacks_folder, os.path.join(destination, "resourcepacks"))
                return

            # Loop through changelog files in reverse order
            for changelog in reversed(os.listdir(changelog_dir_path)):
                if changelog.endswith(('.yml', '.yaml')):  # Process only YAML files
                    version = str(changelog_factory.get_changelog_value(changelog, "version"))
                    version_path = os.path.join(tempgit_path, version) # Set the path for the version
                    
                    # Download files if version is not current and folder doesn't exist
                    if version != pack_version and not os.path.exists(version_path):
                        os.makedirs(version_path)
                        try:
                            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # Ensure Windows compatibility
                            asyncio.run(download_compare_files_async(version, version_path))  # Run the async download
                        except Exception as ex:
                            print(ex)  # Print errors if any occur


        #----------------------------------------
        # Generate CHANGELOG.md file.
        #----------------------------------------

        if settings.generate_primary_changelog:
            os.chdir(git_path)
            changelog_factory.build_markdown_changelog(settings.repo_owner, settings.repo_name, tempgit_path, packwiz_path, repo_branch = settings.repo_main_branch, mc_version=minecraft_version)


        #----------------------------------------
        # Generate mod changes comparison files.
        #----------------------------------------

        # if settings.generate_mods_changelog:
        #     os.chdir(git_path)
        #     changelog_files = os.listdir(changelog_dir_path)
            
        #     # Create a list of (filename, version) tuples for sorting
        #     version_file_pairs = []
        #     for changelog in changelog_files:
        #         if changelog.endswith(('.yml', '.yaml')):
        #             ver = changelog_factory.get_changelog_value(changelog, 'version')
        #             version_file_pairs.append((changelog, str(ver)))
            
        #     # Sort based on version numbers, handling letter suffixes
        #     sorted_pairs = sorted(
        #         version_file_pairs,
        #         key=lambda x: version_helper.parse(changelog_factory.normalize_version(x[1])),
        #         reverse=True
        #     )
            
        #     # Convert back to just filenames, maintaining the correct order
        #     changelog_list = [pair[0] for pair in sorted_pairs]

        #     # Iterate over the sorted list with an index using enumerate
        #     for i, changelog in enumerate(changelog_list):
        #         # Check if there's a "next" item
        #         if i + 1 < len(changelog_list):
        #             next_changelog = changelog_list[i + 1]
        #         else:
        #             next_changelog = None  # No next item if we're at the last one
                
        #         current_version = changelog_factory.get_changelog_value(changelog, 'version')
        #         if next_changelog:
        #             next_version = changelog_factory.get_changelog_value(next_changelog, 'version')

        #         next_version_path = os.path.join(tempgit_path, str(next_version))
        #         current_version_path = os.path.join(tempgit_path, str(current_version))

        #         if str(current_version) != str(pack_version) and next_version:
        #             differences = changelog_factory.compare_toml_files(next_version_path, current_version_path)
        #         elif str(current_version) == str(pack_version) and next_version:
        #             differences = changelog_factory.compare_toml_files(next_version_path, packwiz_mods_path)
        #         else:
        #             differences = None

        #         if next_version != current_version:
        #             markdown.write_differences_to_markdown(
        #                 differences,
        #                 modpack_name,
        #                 next_version,
        #                 current_version,
        #                 os.path.join(git_path, 'Changelogs', f'changelog_mods_{current_version}.md')
        #             )
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
                pw_release_type = "beta"
                pw_prerelease = True

            elif "alpha" in pack_version:
                pw_release_type = "alpha"
                pw_prerelease = True
            else:
                pw_release_type = "release"
                pw_prerelease = False
            
            publish_workflow_yml['env']['RELEASE_TYPE'] = pw_release_type
            publish_workflow_yml['env']['PRE_RELEASE'] = pw_prerelease

            with open(publish_workflow_path, "w") as pw_file:
                yaml2.dump(publish_workflow_yml, pw_file)
        

        #----------------------------------------
        # Create release notes.
        #----------------------------------------

        # Parse the related changelog file for overview details and create release markdown files for CF and MR.
        if settings.create_release_notes:
            os.chdir(git_path)
            changelog_path = os.path.join(git_path, "Changelogs", f"{pack_version}+{minecraft_version}.yml")
            
            major_minecraft_version = '.'.join(minecraft_version.split('.', 2)[:2])

            md_element_full_changelog = f"**[[Full Changelog]](https://crismpack.net/{modpack_name.lower().split(' ', 1)[0]}/changelogs/{major_minecraft_version}/{minecraft_version}#v{pack_version})**"
            md_element_pre_release = '**This is a pre-release. Here be dragons!**'
            md_element_bh_banner = f"[![BisectHosting Banner]({settings.bh_banner})](https://bisecthosting.com/CRISM)"
            md_element_crism_spacer = "![CrismPack Spacer](https://github.com/CrismPack/CDN/blob/main/desc/breakneck/79ESzz1-tiny.png?raw=true)"
            # html_element_settings.bh_banner = "<p><a href='https://bisecthosting.com/CRISM'><img src='https://github.com/CrismPack/CDN/blob/main/desc/insomnia/bhbanner.png?raw=true' width='800' /></a></p>"

            
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
                # Client
                with open(bcc_client_config_path, "r") as f:
                    bcc_json = json.load(f)
                bcc_json["modpackVersion"] = pack_version
                with open(bcc_client_config_path, "w") as f:
                    json.dump(bcc_json, f)
            # Server
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

            # Write the JSON output to the file
            with open(crash_assistant_config_path, "w", encoding="utf8") as output_file:
                output_file.write(mod_filenames_json)
        

        #----------------------------------------
        # Export client pack. (CurseForge with Packwiz)
        #----------------------------------------
        os.chdir(packwiz_path)

        # Refresh the packwiz index
        subprocess.call(f"{packwiz_exe_path} refresh", shell=True)

        # Packwiz exporting
        file = f'{modpack_name}-{pack_version}.zip'
        if settings.export_client and settings.breakneck_fixes == False:
            # Export CF modpack using Packwiz.
            subprocess.call(f"{packwiz_exe_path} cf export", shell=True)
            move(file, os.path.join(export_path, file))
            print("[PackWiz] Client exported.")



        #----------------------------------------
        # Export client pack. (CurseForge & Modrinth with MMC)
        # Breakneck only.
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
                
                # Parse mod toml files for (disabled) marker.
                for item in os.listdir():
                    if os.path.isdir(item) and item == "disabled":
                        continue  # Skip the 'disabled' directory itself
                    try:
                        with open(item, "r") as f:
                            mod_toml = toml.load(f)
                            if "disabled" in mod_toml["side"]:
                                f.close()
                                move(item, disabled_mods_path)
                    except OSError as e:
                        print(f"move_disabled_mods: {e}")
                os.chdir(packwiz_path)
                subprocess.call(f"{packwiz_exe_path} refresh", shell=True) # Refresh the packwiz index
            

            # Creates mmc-cache folder if it doesn't already exist and ensure that it is empty.
            try:
                os.mkdir(mmc_cache_path)
            except:
                pass
            clear_mmc_cache(mmc_cache_path)


            file = Path(mmc_cache_path + "packwiz-installer.jar")
            if bootstrap_nogui:
                if file.is_file():
                    # Export Packwiz modpack to MMC cache folder and zip it.
                    subprocess.call(f"java -jar \"{packwiz_installer_path}\" -s {packwiz_side} \"{os.path.join(packwiz_path, packwiz_manifest)}\" -g --bootstrap-no-update", shell=True)
                else:
                    # Export Packwiz modpack to MMC cache folder and zip it.
                    subprocess.call(f"java -jar \"{packwiz_installer_path}\" -s {packwiz_side} \"{os.path.join(packwiz_path, packwiz_manifest)}\" -g", shell=True)
            else:
                if file.is_file():
                    # Export Packwiz modpack to MMC cache folder and zip it.
                    subprocess.call(f"java -jar \"{packwiz_installer_path}\" -s {packwiz_side} \"{os.path.join(packwiz_path, packwiz_manifest)}\" --bootstrap-no-update", shell=True)
                else:
                    # Export Packwiz modpack to MMC cache folder and zip it.
                    subprocess.call(f"java -jar \"{packwiz_installer_path}\" -s {packwiz_side} \"{os.path.join(packwiz_path, packwiz_manifest)}\"", shell=True)

            # Creates mmc\.minecraft folder if it doesn't already exist.
            try:
                os.mkdir(mmc_dotminecraft_path)
            except:
                pass
            
            
            # Moves override folders into .minecraft folder
            move_list = ["shaderpacks", "resourcepacks", "mods", "config"]
            for item in os.listdir(os.getcwd()):
                if item in move_list:
                    move(item, mmc_dotminecraft_path)

            
            if move_disabled_mods:
                os.chdir(disabled_mods_path)
                retain = [".gitkeep"] # Files that shouldn't be deleted
                try:
                    # Moves disabled mods back.
                    for item in os.listdir():
                        if item not in retain:
                            move(item, mods_path)
                except OSError as e:
                    print(e)
                os.chdir(packwiz_path)
            
            
            make_archive("mcc-cache", 'zip', mmc_cache_path) # Creates mcc-cache.zip file based on mmc-cache folder.
            
            # Export Modrinth modpack using MMC method.
            if export_mmc_modrinth:
                print("[MMC] Exporting Modrinth...")
                args = (
                    "mmc-export",
                    "--input", mmc_input_path,
                    "--format", "Modrinth",
                    "--modrinth-search", "loose",
                    "-o", export_path,
                    "-c", mmc_config,
                    "-v", pack_version,
                    "--scheme", modpack_name + "-" + minecraft_version + "-{version}",
                ); subprocess.call(args, shell=True)
                print("[MMC] Modrinth exported.")

            # Export CurseForge modpack using MMC method.
            if export_mmc_curseforge:
                print("[MMC] Exporting CurseForge...")
                args = (
                    "mmc-export",
                    "--input", mmc_input_path,
                    "--format", "CurseForge",
                    "-o", export_path,
                    "-c", mmc_config,
                    "-v", pack_version,
                    "--scheme", modpack_name + "-" + minecraft_version + "-{version}",
                ); subprocess.call(args, shell=True)
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
            # Export CF modpack using Packwiz.
            subprocess.call(f"{packwiz_exe_path} cf export -s server", shell=True)
            file_server_name = f'{modpack_name}-Server-{pack_version}.zip'
            move(file, f"{export_path}{file_server_name}")
            print("[PackWiz] Server exported.")

            os.chdir(git_path)
            # Deletes the temp folder if it already exists.
            if os.path.isdir(tempfolder_path):
                rmtree(tempfolder_path)

            copytree("Server Pack", tempfolder_path) # Copies contents of "Server Pack" folder into the temp folder.

            # Console input.
            server_mods_path = input(f'Create a new modpack instance in the CurseForge launcher using the {file_server_name} file. Then drag the mods folder from that instance into the terminal (No spaces allowed for the source directory): ')
            
            copytree(server_mods_path, temp_mods_path, dirs_exist_ok=True)
            
            # Removes specified files from mods folder
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