import shutil

from modpack_tool import project

from .conftest import FIXTURES, metafile, write


def _legacy_project(tmp_path, fixture, monkeypatch, answer=""):
    root = tmp_path / "Pack"
    write(root / "Packwiz" / "pack.toml", 'name = "InsomniaHardcore"\nversion = "2.2.0"\n'
          '[versions]\nfabric = "0.18.4"\nminecraft = "1.21.1"\n')
    write(root / "Packwiz" / "mods" / "from-the-fog.pw.toml",
          metafile("From The Fog", "From-The-Fog-1.21-v2.0.jar"))
    shutil.copy(FIXTURES / "legacy_settings" / fixture, root / "settings.yml")
    monkeypatch.setattr("builtins.input", lambda prompt="": answer)
    return root


def test_import_breakneck_settings(tmp_path, monkeypatch):
    root = _legacy_project(tmp_path, "breakneck.yml", monkeypatch)
    settings, notes = project.load_settings(root, "Breakneck")
    assert settings.exports == ["curseforge", "modrinth"]  # breakneck_fixes implied both platforms
    assert settings.side_tags is False
    assert settings.alpha_updates == "prompt"
    assert settings.changelog_url == "https://crismpack.net/breakneck/changelogs/{mc_group}#{anchor}"
    assert "bh.png" in settings.curseforge_notes_footer
    assert any("Imported settings" in note for note in notes)
    assert (root / "modpack-tool.yml").is_file()


def test_import_insomnia_settings_and_fix_old_exclusion(tmp_path, monkeypatch):
    root = _legacy_project(tmp_path, "insomnia.yml", monkeypatch, answer="insomnia")
    settings, notes = project.load_settings(root, "InsomniaHardcore")
    assert settings.exports == ["curseforge", "server"]
    assert settings.side_tags is True
    assert settings.server_exclude == ["from-the-fog"]
    assert settings.changelog_url == "https://crismpack.net/insomnia/changelogs/{mc_group}#{anchor}"
    assert any("old filename" in note for note in notes)


def test_settings_file_is_stable_and_keeps_edits(tmp_path, monkeypatch):
    root = _legacy_project(tmp_path, "insomnia.yml", monkeypatch, answer="insomnia")
    project.load_settings(root, "InsomniaHardcore")
    path = root / "modpack-tool.yml"
    first = path.read_bytes()
    project.load_settings(root, "InsomniaHardcore")
    assert path.read_bytes() == first

    text = path.read_text().replace('alpha_updates: "prompt"', 'alpha_updates: "never"')
    path.write_text(text + "\nmystery_key: 1\n")
    settings, notes = project.load_settings(root, "InsomniaHardcore")
    assert settings.alpha_updates == "never"
    assert any("mystery_key" in note for note in notes)
    assert "mystery_key" not in path.read_text()
    assert "# When an update lands on an alpha version" in path.read_text()


def test_new_project_gets_template(tmp_path):
    root = tmp_path / "Fresh"
    write(root / "Packwiz" / "pack.toml", 'name = "Fresh Pack"\nversion = "1.0.0"\n[versions]\nminecraft = "26.1"\n')
    settings, notes = project.load_settings(root, "Fresh Pack")
    assert settings.exports == ["curseforge", "modrinth"]
    assert settings.changelog_url == "https://crismpack.net/fresh/changelogs/{mc_group}#{anchor}"
    assert any("Created" in note for note in notes)


def test_bad_values_fall_back(tmp_path):
    root = tmp_path / "Odd"
    write(root / "modpack-tool.yml", 'exports: [curseforge, itch]\nalpha_updates: "sometimes"\n')
    settings, notes = project.load_settings(root, "Odd")
    assert settings.exports == ["curseforge"]
    assert settings.alpha_updates == "prompt"
    assert len(notes) == 2


def test_open_project_reads_pack_info(pack_dir):
    proj, _ = project.open_project(pack_dir.parent, project.ToolConfig())
    assert (proj.name, proj.version, proj.minecraft) == ("MyPack", "1.2.0", "1.21.11")
    assert (proj.loader, proj.loader_version, proj.loader_label) == ("fabric", "0.18.4", "Fabric")
    assert not proj.mc_prefixed


def test_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(project, "TOOL_CONFIG_PATH", tmp_path / "tool_config.yml")
    config = project.load_tool_config()
    project.remember_project(config, tmp_path / "A")
    project.remember_project(config, tmp_path / "B")
    project.remember_project(config, tmp_path / "a")  # Same folder on Windows; no duplicate there.
    loaded = project.load_tool_config()
    assert loaded.last_used_project.lower().endswith("a")
    project.forget_project(loaded, tmp_path / "B")
    assert all(not root.endswith("B") for root in project.load_tool_config().projects)
