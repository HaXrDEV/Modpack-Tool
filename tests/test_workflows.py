import json

import pytest

from modpack_tool import __main__ as cli
from modpack_tool import changelog, mods, pack, platforms, project, release
from modpack_tool.ui import ToolError

from .conftest import git, metafile, write


class FakePackwiz:
    """Records packwiz calls instead of running packwiz."""

    def __init__(self, pack_dir):
        self.pack_dir = pack_dir
        self.exe = "packwiz"
        self.calls = []

    def refresh(self):
        self.calls.append("refresh")

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, *args))


@pytest.fixture
def repo_project(tmp_path, monkeypatch, have_git):
    """A pack in a git repo: release 1.0.0 is tagged, the working copy is 1.1.0."""
    root = tmp_path / "Pack"
    pw = root / "Packwiz"
    write(pw / "pack.toml", 'name = "Pack"\nversion = "1.0.0"\n[index]\nfile = "index.toml"\nhash = "h1"\n'
          '[versions]\nfabric = "0.18.4"\nminecraft = "1.21.11"\n')
    write(pw / "index.toml", 'hash-format = "sha256"\n')
    write(pw / "mods" / "a.pw.toml", metafile("Alpha Mod", "a-1.jar"))
    write(pw / "config" / "bcc.json", '{"modpackVersion": "1.0.0"}')
    write(pw / "config" / "crash_assistant" / "config.toml", "")
    write(root / "modlist.md", "old")
    write(root / "Changelogs" / "1.0.0+1.21.11.yml", "Update overview:\n  - First.\n")
    write(root / "modpack-tool.yml", "exports: []\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "Test")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "1.0.0")
    git(root, "tag", "1.0.0")
    monkeypatch.setattr(project, "TOOL_CONFIG_PATH", tmp_path / "tool_config.yml")
    proj, _ = project.open_project(root, project.ToolConfig())
    proj.packwiz = FakePackwiz(pw)
    return proj


def answers(monkeypatch, *values):
    replies = iter(values)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(replies))


def test_new_version_bumps_after_a_release(repo_project, monkeypatch):
    answers(monkeypatch, "")  # Accept the suggested 1.0.1.
    assert release.new_version(repo_project)
    assert repo_project.version == "1.0.1"
    assert (repo_project.changelog_dir / "1.0.1+1.21.11.yml").is_file()
    assert json.loads((repo_project.pack_dir / "config" / "bcc.json").read_text())["modpackVersion"] == "1.0.1"


def test_new_version_renames_an_unreleased_version(repo_project, monkeypatch):
    release.new_version(repo_project, version="1.1.0")
    changelog.write_record(repo_project, {"version": "1.1.0"})
    answers(monkeypatch, "r")
    release.new_version(repo_project, version="2.0.0")
    assert not (repo_project.changelog_dir / "1.1.0+1.21.11.yml").exists()
    assert (repo_project.changelog_dir / "2.0.0+1.21.11.yml").is_file()
    record = json.loads((repo_project.data_dir / "2.0.0+1.21.11.json").read_text(encoding="utf-8"))
    assert record["version"] == "2.0.0"


def test_new_version_refuses_a_released_version(repo_project):
    release.new_version(repo_project, version="1.1.0")
    with pytest.raises(ToolError, match="already released"):
        release.new_version(repo_project, version="1.0.0")


def test_changes_since_release_and_draft(repo_project, monkeypatch):
    release.new_version(repo_project, version="1.1.0")
    write(repo_project.pack_dir / "mods" / "b.pw.toml", metafile("Beta Mod", "b-1.jar"))
    changes, base = release.changes_since_release(repo_project)
    assert base == "1.0.0" and [n.name for n in changes.mods.added] == ["Beta Mod"]
    assert release.draft(repo_project)
    data = changelog.load_changelog(changelog.changelog_path(repo_project))
    assert changelog.section_lines(data["Update overview"]) == ["Added 'Beta Mod' mod."]


def test_build_writes_record_notes_and_pack_files(repo_project, monkeypatch):
    release.new_version(repo_project, version="1.1.0")
    write(repo_project.pack_dir / "mods" / "b.pw.toml", metafile("Beta Mod", "b-1.jar"))
    answers(monkeypatch, "y")  # Draft the empty sections.
    release.build(repo_project, review=False)
    record = json.loads((repo_project.data_dir / "1.1.0+1.21.11.json").read_text(encoding="utf-8"))
    assert record["mods"]["added"] == ["Beta Mod"] and record["overview"] == ["Added 'Beta Mod' mod."]
    assert (repo_project.root / "CurseForge-Release.md").read_text().startswith("- Added 'Beta Mod' mod.")
    assert "- Beta Mod" in (repo_project.root / "modlist.md").read_text()
    modlist = repo_project.pack_dir / "config" / "crash_assistant" / "modlist.json"
    assert json.loads(modlist.read_text()) == ["a-1.jar", "b-1.jar"]
    last = json.loads((repo_project.export_dir / release.LAST_BUILD_FILE).read_text())
    assert last == {"version": "1.1.0", "index_hash": "h1", "files": []}
    assert cli.next_step(repo_project, changelog.load_changelog(changelog.changelog_path(repo_project))) == "Publish (5)."


def test_build_refuses_an_empty_changelog(repo_project, monkeypatch):
    release.new_version(repo_project, version="1.1.0")
    answers(monkeypatch, "n")  # Don't draft.
    with pytest.raises(ToolError, match="is empty"):
        release.build(repo_project, review=False)


def test_publish_guards(repo_project, monkeypatch, capsys):
    with pytest.raises(ToolError, match="already released"):
        release.publish(repo_project)
    release.new_version(repo_project, version="1.1.0")
    with pytest.raises(ToolError, match="no build"):
        release.publish(repo_project)
    write(changelog.changelog_path(repo_project), "Bug Fixes:\n  - Fixed it.\n")
    answers(monkeypatch, "n")  # Don't draft the empty sections.
    release.build(repo_project, review=False)
    monkeypatch.setattr("shutil.which", lambda name: "gh")
    release.publish(repo_project, dry_run=True)
    output = capsys.readouterr().out
    assert 'git commit -m "Release 1.1.0"' in output and "gh release create 1.1.0" in output
    assert "--notes-file Modrinth-Release.md --target main" in output
    pack.set_pack_version(repo_project.pack_dir, "1.1.0")  # Same version, but pretend the index changed:
    (repo_project.pack_dir / "pack.toml").write_text(
        (repo_project.pack_dir / "pack.toml").read_text().replace('hash = "h1"', 'hash = "h2"'))
    with pytest.raises(ToolError, match="changed since the last build"):
        release.publish(repo_project)


def test_alpha_guard_redirects_and_reverts(pack_dir, monkeypatch):
    proj, _ = project.open_project(pack_dir.parent, project.ToolConfig())
    proj.packwiz = FakePackwiz(pack_dir)
    proj.settings.alpha_updates = "never"
    all_mods = pack.load_mods(pack_dir)
    before = pack.snapshot_texts(pack_dir, all_mods)
    # "Update" two Modrinth mods to new versions.
    for slug, new_version in (("lithium", "LithNEW"), ("sodium", "SodiNEW")):
        path = pack_dir / "mods" / f"{slug}.pw.toml"
        path.write_bytes(path.read_bytes().replace(f'version = "{slug.capitalize()[:4]}v1"'.encode(),
                                                   f'version = "{new_version}"'.encode()))
    types = {"Lithv1": "release", "LithNEW": "alpha", "Sodiv1": "beta", "SodiNEW": "release"}
    monkeypatch.setattr(platforms, "modrinth_versions",
                        lambda ids: {i: {"id": i, "version_type": types[i]} for i in ids if i in types})
    monkeypatch.setattr(platforms, "curseforge_files", lambda ids: {})
    monkeypatch.setattr(platforms, "modrinth_project_versions", lambda *a: [
        {"id": "LithALPHA2", "version_type": "alpha"},
        {"id": "LithBETA", "version_type": "beta", "version_number": "0.22-beta", "files": [
            {"primary": True, "url": "https://x/l.jar", "filename": "lithium-0.22-beta.jar", "hashes": {"sha512": "b"}}]}])
    mods._after_update(proj, before, migration=False)
    lithium = next(m for m in pack.load_mods(pack_dir) if m.slug == "lithium")
    sodium = next(m for m in pack.load_mods(pack_dir) if m.slug == "sodium")
    assert lithium.modrinth["version"] == "LithBETA" and lithium.filename == "lithium-0.22-beta.jar"
    assert sodium.modrinth["version"] == "SodiNEW"  # beta -> release is fine.


def test_incompatible_mods(pack_dir, monkeypatch):
    proj, _ = project.open_project(pack_dir.parent, project.ToolConfig())
    monkeypatch.setattr(platforms, "modrinth_versions", lambda ids: {
        "Lithv1": {"game_versions": ["1.21.11"]}, "Sodiv1": {"game_versions": ["1.21.1", "1.21.10"]},
        "Pinnv1": {"game_versions": ["1.21.11"]}})
    monkeypatch.setattr(platforms, "curseforge_files", lambda ids: {})
    incompatible, unknown = mods.incompatible_mods(proj)
    assert [m.slug for m in incompatible] == ["sodium"]  # 1.21.1 isn't 1.21.11.
    assert [m.slug for m in unknown] == ["server"]
    proj.acceptable_versions = ["1.21.10"]
    assert mods.incompatible_mods(proj)[0] == []


def test_cli_parser_lists_every_command():
    parser = cli.build_parser()
    args = parser.parse_args(["--project", "X", "build", "--skip-server", "--since", "4.11.0"])
    assert (args.project, args.command, args.skip_server, args.since) == ("X", "build", True, "4.11.0")
    assert {c.name for c in cli.COMMANDS} == {"update", "new-version", "draft", "build", "publish", "migrate", "check"}
    assert len({c.key for c in cli.COMMANDS}) == len(cli.COMMANDS)
