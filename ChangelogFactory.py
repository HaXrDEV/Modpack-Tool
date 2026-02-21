import os

from ruamel.yaml.error import YAMLError

from mdutils.mdutils import MdUtils
import re
import toml
import MarkdownHelper as markdown
from packaging import version as version_helper
import requests
from datetime import datetime

class ChangelogFactory:
    def __init__(self, changelog_dir, modpack_name, modpack_version, settings, yaml_instance):
        self.changelog_dir = changelog_dir
        self.modpack_name = modpack_name
        self.modpack_version = modpack_version
        self.settings = settings
        self.yaml = yaml_instance
        
    def get_changelog_value(self, changelog_yml, key):
        if changelog_yml and changelog_yml.endswith(('.yml', '.yaml')):
            file_path = os.path.join(self.changelog_dir, changelog_yml)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    changelog_data = self.yaml.load(f) or {}
                return changelog_data[key]
            except YAMLError as e:
                print(f"Error parsing {file_path}: {e}")
            except KeyError:
                print(f"Key '{key}' not found in {file_path}")
            except OSError as e:
                print(f"Error reading {file_path}: {e}")



    def compare_toml_files(self, dir1, dir2):
        # Initialize dictionaries to store TOML data
        toml_data_1 = {}
        toml_data_2 = {}
        
        def local_load_toml_files_from_dir(dir, dict):
            try:
                for filename in os.listdir(dir):
                    if filename.endswith('.toml'):
                        filepath = os.path.join(dir, filename)
                        with open(filepath, "r", encoding="utf8") as f:
                            mod_toml = toml.load(f)
                            side = str(mod_toml['side'])
                            if side in ("both", "client", "server"):
                                dict[filename] = toml.load(filepath)
            except Exception as ex:
                print(ex)

        local_load_toml_files_from_dir(dir1, toml_data_1)
        local_load_toml_files_from_dir(dir2, toml_data_2)

        # Prepare to store results
        results = {
            'added': [],
            'removed': [],
            'modified': []
        }
        def local_get_side_str(side):
            if side != "both" and self.settings.changelog_side_tag:
                return f" `{str(side).capitalize()}`"
            else:
                return ""

        # Temporary lists to store names for comparison
        added_names = []
        removed_names = []
        
        # First pass: collect all names
        for filename, data in toml_data_2.items():
            if filename not in toml_data_1:
                name = markdown.remove_bracketed_text(data.get('name', filename))
                side_data = data.get('side', filename)
                side_str = local_get_side_str(side_data)
                added_names.append((name, name + side_str))

        for filename in toml_data_1.keys():
            if filename not in toml_data_2:
                name = markdown.remove_bracketed_text(toml_data_1[filename].get('name', filename))
                side_data = toml_data_1[filename].get('side', filename)
                side_str = local_get_side_str(side_data)
                removed_names.append((name, name + side_str))

        # Find names that appear in both lists
        added_base_names = set(name[0] for name in added_names)
        removed_base_names = set(name[0] for name in removed_names)
        duplicates = added_base_names.intersection(removed_base_names)

        # Second pass: add to results, excluding duplicates
        for base_name, full_name in added_names:
            if base_name not in duplicates:
                results['added'].append(full_name)

        for base_name, full_name in removed_names:
            if base_name not in duplicates:
                results['removed'].append(full_name)

        # Handle modified files (unchanged from original)
        for filename, data in toml_data_2.items():
            if filename in toml_data_1:
                version1 = toml_data_1[filename].get('filename', None)
                version2 = data.get('filename', None)
                if version1 != version2:
                    results['modified'].append((markdown.remove_bracketed_text(data.get('name', filename)), version1, version2))

        return results

    def get_previous_version_for_mc(self, target_version, mc_version):
        version_candidates = []
        for changelog in os.listdir(self.changelog_dir):
            if not changelog.endswith((".yml", ".yaml")):
                continue
            try:
                if str(self.get_changelog_value(changelog, "mc_version")) != str(mc_version):
                    continue
                current_version = str(self.get_changelog_value(changelog, "version"))
                version_candidates.append(current_version)
            except Exception:
                continue

        if not version_candidates:
            return None

        sorted_versions = sorted(
            version_candidates,
            key=lambda x: version_helper.parse(self.normalize_version(str(x))),
            reverse=True,
        )
        target_parsed = version_helper.parse(self.normalize_version(str(target_version)))
        for candidate in sorted_versions:
            candidate_parsed = version_helper.parse(self.normalize_version(candidate))
            if candidate_parsed < target_parsed:
                return candidate
        return None

    def get_current_pack_diff_payload(self, target_version, mc_version, tempgit_path, packwiz_path):
        previous_version = self.get_previous_version_for_mc(target_version, mc_version)
        if not previous_version:
            return None

        previous_version_path = os.path.join(tempgit_path, str(previous_version))
        previous_mods_path = os.path.join(previous_version_path, "mods")
        previous_resourcepacks_path = os.path.join(previous_version_path, "resourcepacks")

        current_mods_path = os.path.join(packwiz_path, "mods")
        current_resourcepacks_path = os.path.join(packwiz_path, "resourcepacks")

        if not os.path.isdir(previous_mods_path) or not os.path.isdir(previous_resourcepacks_path):
            return None

        mod_differences = self.compare_toml_files(previous_mods_path, current_mods_path)
        resourcepack_differences = self.compare_toml_files(previous_resourcepacks_path, current_resourcepacks_path)

        return {
            "previous_version": previous_version,
            "current_version": str(target_version),
            "mc_version": str(mc_version),
            "mod_differences": mod_differences,
            "resourcepack_differences": resourcepack_differences,
        }

    def sort_versions(self, version_list):
        """
        Sort versions according to semantic versioning rules, handling post-releases correctly.
        Returns list in descending order (newest first).
        """
        return sorted(version_list, key=lambda x: version_helper.parse(self.normalize_version(str(x))), reverse=True)


    def normalize_version(self, version_str):
        """
        Normalize version strings to handle letter suffixes as post-releases.
        Examples:
            4.1.1a -> 4.1.1.post1
            4.1.1b -> 4.1.1.post2
            etc.
        """
        # Regular expression to match version with optional letter suffix
        match = re.match(r'^(\d+\.\d+\.\d+)([a-zA-Z])?$', str(version_str))
        if match:
            base_version, letter_suffix = match.groups()
            if letter_suffix:
                # Convert letter to number (a=1, b=2, etc.) and use as post-release number
                post_number = ord(letter_suffix.lower()) - ord('a') + 1
                return f"{base_version}.post{post_number}"
        return str(version_str)



    def Reverse(self, lst):
        new_lst = lst[::-1]
        return new_lst


    def vitepress_container_maker(self, type: str, content: str):
        """https://vitepress.dev/guide/markdown#custom-containers"""
        return(
            f"::: {type}\n"
            f"{content}\n"
            f":::"
        )

    def fetch_modrinth_versions(self, modpack_slug):
        """
        Fetches all version data for a given modpack from the Modrinth API.

        Args:
            modpack_slug (str): The slug of the modpack.

        Returns:
            list: A list of version data in JSON format, or None if an error occurs.
        """
        try:
            api_url = f"https://api.modrinth.com/v2/project/{modpack_slug}/version"
            response = requests.get(api_url)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching data from API: {e}")
            return None


    def extract_modrinth_version_info(self, versions, version_number=None):
        """
        Extracts version information from the fetched JSON data.

        Args:
            versions (list): The list of version data in JSON format.
            version_number (str, optional): The specific version number to find. Defaults to None.

        Returns:
            dict: A dictionary containing version details, or None if not found.
        """
        if not versions:
            print("No version data available.")
            return None

        if version_number:
            for version in versions:
                if version["version_number"] == version_number:
                    return {
                        "name": version["name"],
                        "version_number": version["version_number"],
                        "date_published": version["date_published"],
                        "download_url": version["files"][0]["url"] if version["files"] else None,
                        "changelog": version["changelog"]
                    }
            print(f"Version {version_number} not found.")
            return None

        # Find the latest version by date_published
        latest_version = max(versions, key=lambda v: v["date_published"])
        return {
            "name": latest_version["name"],
            "version_number": latest_version["version_number"],
            "date_published": latest_version["date_published"],
            "download_url": latest_version["files"][0]["url"] if latest_version["files"] else None,
            "changelog": latest_version["changelog"]
        }

    def build_markdown_changelog(self, repo_owner, repo_name, tempgit_path, packwiz_path, file_name="CHANGELOG", repo_branch = "main", mc_version=None):
        mdFile = MdUtils(file_name)

        changelog_files = os.listdir(self.changelog_dir)
        modrinth_versions = self.fetch_modrinth_versions(self.modpack_name)
        
        # Create a list of (filename, version) tuples for sorting
        version_file_pairs = []
        for changelog in changelog_files:
            if changelog.endswith(('.yml', '.yaml')):
                ver = self.get_changelog_value(changelog, 'version')
                version_file_pairs.append((changelog, str(ver)))
        
        # Sort based on version numbers, handling letter suffixes
        sorted_pairs = sorted(
            version_file_pairs,
            key=lambda x: version_helper.parse(self.normalize_version(x[1])),
            reverse=True
        )
        
        # Convert back to just filenames, maintaining the correct order
        changelog_list = [pair[0] for pair in sorted_pairs]




        # Iterate over the list with an index using enumerate
        mdFile.new_paragraph(f"##### {self.modpack_name}")

        if mc_version:
            mdFile.new_paragraph(f"# Changelog - {mc_version}")
        else:
            mdFile.new_paragraph(f"# Changelog")

        for i, changelog in enumerate(changelog_list):
            # Check if there's a "next" item
            if i + 1 < len(changelog_list):
                next_changelog = changelog_list[i + 1]
            else:
                next_changelog = None  # No next item if we're at the last one


            added_mods = None
            removed_mods = None

            if changelog.endswith(('.yml', '.yaml')) and mc_version == self.get_changelog_value(changelog, 'mc_version'): # Only takes yaml files and those with the correct mc version.
                version = self.get_changelog_value(changelog, "version")
                if next_changelog:
                    next_version = self.get_changelog_value(next_changelog , "version")
                    next_mc_version = self.get_changelog_value(next_changelog, "mc_version")

                fabric_loader = self.get_changelog_value(changelog, "Fabric version")
                improvements = self.get_changelog_value(changelog, "Changes/Improvements")
                overview_legacy = self.get_changelog_value(changelog, "Update overview")
                bug_fixes = self.get_changelog_value(changelog, "Bug Fixes")
                config_changes = self.get_changelog_value(changelog, "Config Changes")
                script_changes = self.get_changelog_value(changelog, "Script/Datapack changes")

                next_version_path = os.path.join(tempgit_path, str(next_version))
                next_version_mods_path = os.path.join(next_version_path, "mods")
                next_version_resourcepacks_path = os.path.join(next_version_path, "resourcepacks")

                version_path = os.path.join(tempgit_path, str(version))
                version_mods_path = os.path.join(version_path, "mods")
                version_resourcepacks_path = os.path.join(version_path, "resourcepacks")

                packwiz_mods_path = os.path.join(packwiz_path, "mods")
                packwiz_resourcepacks_path = os.path.join(packwiz_path, "resourcepacks")

                print(f"[DEBUG] {next_version_path} + {version_path}")

                if str(version) != str(self.modpack_version) and next_version:
                    mod_differences = self.compare_toml_files(next_version_mods_path, version_mods_path)
                    resourcepack_differences = self.compare_toml_files(next_version_resourcepacks_path, version_resourcepacks_path)
                elif str(version) == str(self.modpack_version) and next_version:
                    mod_differences = self.compare_toml_files(next_version_mods_path, packwiz_mods_path)
                    resourcepack_differences = self.compare_toml_files(next_version_resourcepacks_path, packwiz_resourcepacks_path)
                else:
                    mod_differences = None
                    resourcepack_differences = None

                if mod_differences:
                    added_mods = mod_differences['added']
                    removed_mods = mod_differences['removed']
                    modified_mods = mod_differences['modified']

                if resourcepack_differences:
                    added_resourcepacks = resourcepack_differences['added']
                    removed_resourcepacks = resourcepack_differences['removed']
                    modified_resourcepacks = resourcepack_differences['modified']
                
                latest_modrinth_version_info = self.extract_modrinth_version_info(modrinth_versions)
                if latest_modrinth_version_info:
                    latest_modrinth_version_number = latest_modrinth_version_info['version_number']
                else:
                    latest_modrinth_version_number = None
                
                date_only = None
                try:
                    current_modrinth_version_info = self.extract_modrinth_version_info(modrinth_versions, version)
                    if current_modrinth_version_info:
                        modrinth_publish_timestamp = current_modrinth_version_info["date_published"]
                        dt_object = datetime.fromisoformat(modrinth_publish_timestamp.replace("Z", ""))
                        date_only = dt_object.strftime("%Y-%m-%d")
                except Exception as e:
                    print(e)
                    continue

                if version == self.modpack_version and not version == latest_modrinth_version_number:
                        mdFile.new_paragraph(f"## v{version} <Badge type='warning' text='Work in progress'/> <a href='#v{version}' id='v{version}'></a>")
                else: 
                    if not "v" in version:
                        mdFile.new_paragraph(f"## v{version} <a href='#v{version}' id='v{version}'></a>")
                    else:
                        mdFile.new_paragraph(f"## {version} <a href='#{version}' id='{version}'></a>")

                
                if date_only:
                    mdFile.new_paragraph(f"<a href='https://github.com/{repo_owner}/{repo_name}/blob/{repo_branch}/Changelogs/changelog_mods_{version}.md'><Badge type='tip' text='Mod Updates'/></a><Badge type='info' text='Fabric Loader {fabric_loader}'/><Badge type='info' text='{date_only}'/>")
                else:
                    mdFile.new_paragraph(f"<a href='https://github.com/{repo_owner}/{repo_name}/blob/{repo_branch}/Changelogs/changelog_mods_{version}.md'><Badge type='tip' text='Mod Updates'/></a><Badge type='info' text='Fabric Loader {fabric_loader}'/>")
                # mdFile.new_paragraph(f"*{date_only}* | *Fabric Loader {fabric_loader}* | *[Mod Updates](https://github.com/{repo_owner}/{repo_name}/blob/{repo_branch}/Changelogs/changelog_mods_{version}.md)*")

                # (Breakneck) Check if it's the second last iteration and prints info box for comparison point.
                if i == len(changelog_list) - 2 and self.settings.breakneck_fixes:
                    mdFile.new_paragraph(self.vitepress_container_maker("info", f"Changes are in comparison to version [{next_version}]({next_mc_version}.md#v{next_version})."))
                
                if "beta" in version or "alpha" in version:
                    mdFile.new_paragraph(self.vitepress_container_maker("warning", "This is a pre-release. Here be dragons!"))

                if improvements:
                    mdFile.new_paragraph("### Changes/Improvements ⭐")
                    mdFile.new_paragraph(markdown.markdown_list_maker(improvements))
                if overview_legacy:
                    mdFile.new_paragraph("### Update Overview ⭐")
                    mdFile.new_paragraph(markdown.markdown_list_maker(overview_legacy))
                if bug_fixes:
                    mdFile.new_paragraph("### Bug Fixes 🪲")
                    mdFile.new_paragraph(markdown.markdown_list_maker(bug_fixes))
                if added_mods:
                    mdFile.new_paragraph("### Added Mods ✅")
                    mdFile.new_paragraph(markdown.markdown_list_maker(added_mods))
                if added_resourcepacks:
                    mdFile.new_paragraph("### Added Resource Packs 📦")
                    mdFile.new_paragraph(markdown.markdown_list_maker(added_resourcepacks))
                if removed_mods:
                    mdFile.new_paragraph("### Removed Mods ❌")
                    mdFile.new_paragraph(markdown.markdown_list_maker(removed_mods))
                if removed_resourcepacks:
                    mdFile.new_paragraph("### Removed Resource Packs ❌")
                    mdFile.new_paragraph(markdown.markdown_list_maker(removed_resourcepacks))

                # Modified mods section
                if mod_differences and mod_differences.get('modified') and self.settings.changelog_updated_mods:
                    mdFile.new_paragraph("### Updated Mods 🔄")
                    mdFile.new_paragraph(markdown.markdown_list_maker([item[0] for item in modified_mods]))
                
                # Modified resource packs section
                if resourcepack_differences and resourcepack_differences.get('modified') and self.settings.changelog_updated_resoucepacks:
                    mdFile.new_paragraph("### Updated Resource Packs 🔃")
                    mdFile.new_paragraph(markdown.markdown_list_maker([item[0] for item in modified_resourcepacks]))

                if script_changes:
                    mdFile.new_paragraph("### Script/Datapack Changes 📝")
                    mdFile.new_paragraph(markdown.markdown_list_maker(script_changes))
                
                if config_changes:
                    mdFile.new_paragraph("### Config Changes 📝")
                    mdFile.new_paragraph(markdown.codify_bracketed_text(config_changes))


                if next_version != version:
                    markdown.write_differences_to_markdown(
                        mod_differences,
                        self.modpack_name,
                        next_version,
                        version,
                        os.path.join('Changelogs', f'changelog_mods_{version}.md')
                    )
        mdFile.create_md_file()
