import json

from modpack_tool import changelog, diff, project

from .test_diff_and_drafting import NEW, OLD


def make_project(pack_dir):
    proj, _ = project.open_project(pack_dir.parent, project.ToolConfig())
    proj.settings.changelog_url = "https://crismpack.net/mypack/changelogs/{mc_group}#{anchor}"
    proj.settings.curseforge_notes_footer = "<br>\n\n[banner](https://example.com)\n"
    return proj


def test_filenames():
    assert changelog.changelog_filename("4.11.1", "1.21.11") == "4.11.1+1.21.11.yml"
    assert changelog.changelog_filename("26.1.1-1.2", "26.1.1", "json") == "26.1.1-1.2.json"
    assert changelog.parse_changelog_filename("4.11.1+1.21.11.yml") == ("4.11.1", "1.21.11")
    assert changelog.parse_changelog_filename("26.1-1.0.yml") == ("26.1-1.0", "26.1")
    assert changelog.parse_changelog_filename("changelog_mods_4.11.1.md") == (None, None)


def test_template_and_sections(pack_dir):
    proj = make_project(pack_dir)
    path = changelog.create_changelog(proj)
    assert path.name == "1.2.0+1.21.11.yml"
    data = changelog.load_changelog(path)
    assert changelog.is_empty(data)
    data["Update overview"] = ["Added 'Sodium' mod."]
    data["Config Changes"] = "- : [mod], [Client]\n- Changed x: [Mod]"
    data["DISCLAIMER"] = "Something important"
    changelog.save_changelog(path, data)
    data = changelog.load_changelog(path)
    assert "# Changelog for MyPack 1.2.0" in path.read_text()
    assert changelog.section_lines(data["Config Changes"]) == ["Changed x: [Mod]"]
    assert changelog.unknown_sections(data) == ["DISCLAIMER"]
    assert not changelog.is_empty(data)


def test_record_matches_the_wiki_contract(pack_dir):
    proj = make_project(pack_dir)
    data = {"Update overview": ["Did things."], "Bug Fixes": "- Fixed a crash", "Mod loader": "Fabric",
            "Script/Datapack changes": ["Cheaper recipe"]}
    result = diff.compare(OLD, NEW, "1.1.0", "1.2.0", "1.21.11")
    record = changelog.build_record(proj, data, result, released="2026-09-25")
    assert list(record) == ["pack", "version", "minecraft", "loader", "released", "prerelease", "comparedTo",
                            "overview", "changes", "bugfixes", "scriptChanges", "configChanges",
                            "mods", "resourcepacks", "shaderpacks"]
    assert record["loader"] == {"name": "Fabric", "version": "0.18.4"}
    assert record["comparedTo"] == {"version": "1.1.0", "minecraft": "1.21.10"}
    assert record["bugfixes"] == ["Fixed a crash"]
    assert record["mods"]["updated"] == [{"name": "Sodium", "from": "sodium-1.jar", "to": "sodium-2.jar"}]
    assert record["prerelease"] is False
    path = changelog.write_record(proj, record)
    assert path.name == "1.2.0+1.21.11.json" and path.parent.name == "data"
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == "1.2.0"


def test_release_notes(pack_dir):
    proj = make_project(pack_dir)
    data = {"Update overview": ["Added 'Sodium' mod.", "Updated mods."]}
    assert changelog.release_notes(proj, data, "curseforge") == (
        "- Added 'Sodium' mod.\n- Updated mods.\n\n"
        "#### **[[Full Changelog]](https://crismpack.net/mypack/changelogs/1.21#v1.2.0)**\n\n"
        "<br>\n\n[banner](https://example.com)\n")
    assert changelog.release_notes(proj, data, "modrinth") == (
        "- Added 'Sodium' mod.\n- Updated mods.\n\n"
        "**[[Full Changelog]](https://crismpack.net/mypack/changelogs/1.21#v1.2.0)**\n")


def test_release_notes_for_prerelease_without_overview(pack_dir):
    proj = make_project(pack_dir)
    proj.version = "1.3.0-beta.1"
    proj.settings.changelog_url = ""
    data = {"Changes/Improvements": ["New menu"], "Bug Fixes": ["Fixed crash"]}
    assert changelog.release_notes(proj, data, "modrinth") == (
        "**This is a pre-release. Here be dragons!**\n\n"
        "### Changes/Improvements ⭐\n\n- New menu\n\n"
        "### Bug Fixes 🪲\n\n- Fixed crash\n")
