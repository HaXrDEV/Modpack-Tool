"""Regression tests for problems found in review: bad input must give clear messages, not crashes."""

import time

import pytest

from modpack_tool import __main__ as cli
from modpack_tool import changelog, diff, exporter, project, release
from modpack_tool.ui import ToolError

from .conftest import write
from .test_workflows import FakePackwiz, answers, repo_project  # noqa: F401  (fixture)


def test_broken_changelog_yaml_is_a_clear_error(tmp_path, capsys, pack_dir):
    path = write(tmp_path / "bad.yml", "Update overview:\n  - `backtick start\n\tTab: x\n")
    with pytest.raises(ToolError, match="isn't valid YAML"):
        changelog.load_changelog(path)
    proj, _ = project.open_project(pack_dir.parent, project.ToolConfig())
    write(changelog.changelog_path(proj), "Update overview:\n  - `oops\n")
    cli.show_status(proj)  # Must not raise.
    assert "isn't valid YAML" in capsys.readouterr().out


def test_mapping_bullets_keep_their_text():
    assert changelog.section_lines(["Sodium: fixed flicker", {"Lithium": "faster"}]) == \
        ["Sodium: fixed flicker", "Lithium: faster"]
    assert changelog.section_lines({"Sodium": "fixed flicker"}) == ["Sodium: fixed flicker"]
    assert changelog.section_lines(42) == ["42"]


def test_settings_values_are_coerced(tmp_path):
    root = tmp_path / "Pack"
    write(root / "modpack-tool.yml", 'exports:\nside_tags: no\nmc_prefixed_versions: "yes"\n'
          'curseforge_notes_footer:\nserver_exclude: from-the-fog\nchangelog_url: [a, b]\n')
    settings, notes = project.load_settings(root, "Pack")
    assert settings.exports == ["curseforge", "modrinth"]  # Empty means the default.
    assert settings.side_tags is False and settings.mc_prefixed_versions is True
    assert settings.curseforge_notes_footer == ""
    assert settings.server_exclude == ["from-the-fog"]
    assert settings.changelog_url == "" and any("single value" in note for note in notes)
    write(root / "modpack-tool.yml", "exports: curseforge\n")
    assert project.load_settings(root, "Pack")[0].exports == ["curseforge"]


def test_broken_settings_yaml_is_a_clear_error(tmp_path):
    root = tmp_path / "Pack"
    write(root / "modpack-tool.yml", 'server_template: "D:\\Servers\\X"\n')
    with pytest.raises(ToolError, match="single quotes"):
        project.load_settings(root, "Pack")


def test_unreadable_tool_config_is_not_overwritten(tmp_path, monkeypatch):
    path = write(tmp_path / "tool_config.yml", 'packwiz_exe_path: "C:\\Users\\me\\packwiz.exe"\nprojects: []\n')
    monkeypatch.setattr(project, "TOOL_CONFIG_PATH", path)
    before = path.read_bytes()
    config = project.load_tool_config()
    assert config.read_only
    project.remember_project(config, tmp_path / "Pack")
    assert path.read_bytes() == before


def test_old_style_publish_workflow_is_kept_in_step(repo_project):  # noqa: F811
    old = ("env:\n  MODRINTH_TOKEN: ${{secrets.MODRINTH_TOKEN}}\n\n  MC_VERSION: 1.21.10\n"
           "  RELEASE_TYPE: release\n  PRE_RELEASE: false\njobs:\n  x:\n    steps:\n"
           "      - with:\n          game-versions: ${{env.MC_VERSION}}\n")
    path = write(repo_project.root / ".github" / "workflows" / "publish.yml", old, newline="\r\n")
    repo_project.version = "1.1.0-beta.1"
    assert release.sync_publish_workflow(repo_project) == path
    text = path.read_bytes().decode()
    assert "  MC_VERSION: 1.21.11\r\n  RELEASE_TYPE: beta\r\n  PRE_RELEASE: true\r\n" in text
    assert "game-versions: ${{env.MC_VERSION}}" in text
    new_style = "env:\n  TAG: ${{github.event.release.tag_name}}\njobs: {}\n"
    write(path, new_style)
    assert release.sync_publish_workflow(repo_project) is None
    assert path.read_text() == new_style


def test_publish_refuses_after_changelog_edits(repo_project, monkeypatch):  # noqa: F811
    release.new_version(repo_project, version="1.1.0")
    path = write(changelog.changelog_path(repo_project), "Bug Fixes:\n  - Fixed it.\n")
    answers(monkeypatch, "n")
    release.build(repo_project, review=False)
    assert release.build_is_current(repo_project) == (True, "")
    write(path, "Bug Fixes:\n  - Fixed it.\n  - And another thing.\n")
    current, reason = release.build_is_current(repo_project)
    assert not current and "changelog changed" in reason
    monkeypatch.setattr("shutil.which", lambda name: "gh")
    with pytest.raises(ToolError, match="changelog changed"):
        release.publish(repo_project, dry_run=True)


def test_config_diff_is_fast_on_big_rewrites():
    old = "\n".join(f'"key{i}": {i},' for i in range(3000)).encode()
    new = "\n".join(f'"key{i}": {i if i % 2 else i + 1},' for i in range(3000)).encode()
    start = time.perf_counter()
    result = diff.diff_config({"big.json": old}, {"big.json": new})
    assert time.perf_counter() - start < 5
    assert result.line_diffs[0]["removed_lines"][:2] == ['"key0": 0,', '"key2": 2,']
    summary = diff.diff_config({"big.json": old}, {"big.json": new}, details=False)
    assert summary.modified == ["big.json"] and summary.line_diffs == []


def test_bundled_report_never_describes_an_old_release(tmp_path, pack_dir, monkeypatch):
    proj, _ = project.open_project(pack_dir.parent, project.ToolConfig())
    report = write(proj.export_dir / "bundled_links.md", "# Bundled files in MyPack 1.0.0\n- [Old](x)\n")
    monkeypatch.setattr(exporter, "PackContents", lambda p: None)
    exporter.export(proj, [])
    assert "No files are bundled" in report.read_text() and "1.2.0" in report.read_text()


def test_status_next_step_after_build(repo_project, monkeypatch):  # noqa: F811
    release.new_version(repo_project, version="1.1.0")
    write(changelog.changelog_path(repo_project), "Bug Fixes:\n  - Fixed it.\n")
    answers(monkeypatch, "n")
    release.build(repo_project, review=False)
    data = changelog.load_changelog(changelog.changelog_path(repo_project))
    assert cli.next_step(repo_project, data) == "Publish (5)."
    write(changelog.changelog_path(repo_project), "Bug Fixes:\n  - Changed.\n")
    data = changelog.load_changelog(changelog.changelog_path(repo_project))
    assert cli.next_step(repo_project, data).startswith("Build release (4) again. The changelog changed")
    assert isinstance(repo_project.packwiz, FakePackwiz)
