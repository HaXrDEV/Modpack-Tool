import json

from modpack_tool import pack

from .conftest import metafile, write


def test_parse_mods_reads_only_direct_metafiles(pack_dir):
    mods = pack.load_mods(pack_dir)
    assert [mod.rel for mod in mods] == [
        "mods/boss.pw.toml", "mods/lithium.pw.toml", "mods/pinned.pw.toml",
        "mods/server.pw.toml", "mods/sodium.pw.toml", "resourcepacks/fresh.pw.toml"]


def test_mod_properties(pack_dir):
    mods = {mod.slug: mod for mod in pack.load_mods(pack_dir)}
    assert mods["boss"].disabled and mods["boss"].base_side == "both" and mods["boss"].curseforge
    assert mods["sodium"].side == "client" and mods["sodium"].installs_on("client")
    assert not mods["sodium"].installs_on("server")
    assert not mods["boss"].installs_on("client")
    assert mods["server"].installs_on("server") and not mods["server"].installs_on("client")
    assert mods["pinned"].pinned and mods["pinned"].display_name == "Pinned Mod"
    assert mods["lithium"].modrinth == {"mod-id": "Lithium", "version": "Lithv1"}
    assert mods["fresh"].category == "resourcepacks" and mods["fresh"].folder == "resourcepacks"


def test_invalid_legacy_and_empty_sides():
    def mod(side):
        return pack.Mod("mods/x.pw.toml", {} if side is None else {"side": side})

    assert mod("dyed(disabled)").disabled and mod("dyed(disabled)").side == "both"
    assert not mod("dyed(disabled)").side_valid
    # Older packs disabled mods with "none" / "both(none)".
    assert mod("none").disabled and mod("both(none)").disabled and mod("none").side_valid
    assert mod("none").side == "both" and mod("both(none)").side == "both"
    assert mod("dyed").disabled  # packwiz wouldn't install a typo'd side either.
    assert mod("").installs_on("server") and mod(None).installs_on("client")


def test_disable_and_enable_keep_formatting_and_line_endings(pack_dir):
    path = pack_dir / "mods" / "sodium.pw.toml"
    original = path.read_bytes()
    sodium = next(m for m in pack.load_mods(pack_dir) if m.slug == "sodium")
    pack.set_disabled(pack_dir, sodium, True)
    changed = path.read_bytes()
    assert b'side = "client(disabled)"\r\n' in changed
    assert changed.replace(b"client(disabled)", b"client") == original
    sodium = next(m for m in pack.load_mods(pack_dir) if m.slug == "sodium")
    pack.set_disabled(pack_dir, sodium, False)
    assert path.read_bytes() == original


def test_apply_modrinth_version(pack_dir):
    lithium = next(m for m in pack.load_mods(pack_dir) if m.slug == "lithium")
    version = {"id": "NEWID", "files": [
        {"primary": False, "url": "https://x/other.jar", "filename": "other.jar", "hashes": {"sha512": "o"}},
        {"primary": True, "url": "https://x/lithium-0.22.jar", "filename": "lithium-0.22.jar",
         "hashes": {"sha1": "s1", "sha512": "s512"}, "size": 1234}]}
    assert pack.apply_modrinth_version(pack_dir, lithium, version)
    lithium = next(m for m in pack.load_mods(pack_dir) if m.slug == "lithium")
    assert lithium.filename == "lithium-0.22.jar"
    assert lithium.hash == ("sha512", "s512")
    assert lithium.download["file-size"] == 1234
    assert lithium.modrinth["version"] == "NEWID"


def test_pack_toml_helpers(pack_dir):
    data = pack.read_pack_toml(pack_dir)
    assert pack.loader_of(data) == ("fabric", "0.18.4")
    pack.set_pack_version(pack_dir, "1.3.0")
    assert pack.read_pack_toml(pack_dir)["version"] == "1.3.0"
    assert 'version = "1.3.0"\n' in (pack_dir / "pack.toml").read_text()
    assert [e["file"] for e in pack.index_entries(pack_dir)] == ["config/bcc.json", "mods/sodium.pw.toml"]
    assert pack.index_hash(pack_dir) == "abc"


def test_generated_files(pack_dir):
    bcc = pack_dir / "config" / "bcc.json"
    assert pack.write_bcc_version(bcc, "1.2.0")
    assert json.loads(bcc.read_text())["modpackVersion"] == "1.2.0"
    assert not pack.write_bcc_version(bcc, "1.2.0")
    assert not pack.write_bcc_version(pack_dir / "missing.json", "1.2.0")

    mods = pack.load_mods(pack_dir)
    assert json.loads(pack.crash_assistant_modlist(mods)) == ["lithium-0.21.jar", "pinned-1.0.jar", "sodium-0.8.jar"]
    assert pack.modlist_markdown(mods, side_tags=False) == (
        "# Mod List\n\n## Active Mods\n- Lithium\n- Pinned Mod\n- Server Thing\n- Sodium\n\n"
        "## Inactive Mods\n- Boss Checklist\n")
    assert "- Sodium [Client]" in pack.modlist_markdown(mods, side_tags=True)


def test_write_text_keeps_existing_line_endings(tmp_path):
    crlf = write(tmp_path / "a.md", "one\ntwo\n", newline="\r\n")
    pack.write_text(crlf, "three\nfour\n")
    assert crlf.read_bytes() == b"three\r\nfour\r\n"
    lf = write(tmp_path / "b.md", "one\n")
    pack.write_text(lf, "x\ny\n")
    assert lf.read_bytes() == b"x\ny\n"


def test_strip_brackets():
    assert pack.strip_brackets("Entity Texture Features [Fabric] (beta)") == "Entity Texture Features"
    assert metafile("A", "a.jar").startswith('name = "A"')
