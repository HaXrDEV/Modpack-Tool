import hashlib
import json
import zipfile

import pytest

from modpack_tool import exporter, platforms, project
from modpack_tool.ui import ToolError

from .conftest import write

JAR = {name: f"{name} jar bytes".encode() for name in ("sodium", "cfmod", "ghmod", "odd", "server", "fa")}


def sha(name, algorithm):
    return hashlib.new(algorithm, JAR[name]).hexdigest()


def modrinth_meta(name, filename, side, folder_hint="mods"):
    return (f'name = "{name.capitalize()}"\nfilename = "{filename}"\nside = "{side}"\n\n[download]\n'
            f'url = "https://cdn.modrinth.com/data/P{name}/versions/V{name}/{filename}"\n'
            f'hash-format = "sha512"\nhash = "{sha(name, "sha512")}"\n\n[update]\n[update.modrinth]\n'
            f'mod-id = "P{name}"\nversion = "V{name}"\n')


@pytest.fixture
def export_project(tmp_path, monkeypatch):
    root = tmp_path / "Pack"
    pw = root / "Packwiz"
    write(pw / "pack.toml", 'name = "Pack"\nauthor = "me"\nversion = "2.0.0"\n[index]\nfile = "index.toml"\n'
          'hash-format = "sha256"\nhash = "h"\n[versions]\nfabric = "0.18.4"\nminecraft = "1.21.11"\n')
    write(pw / "mods" / "sodium.pw.toml", modrinth_meta("sodium", "sodium.jar", "client"))
    write(pw / "mods" / "cfmod.pw.toml",
          f'name = "CF Mod"\nfilename = "cfmod.jar"\nside = "both"\n[download]\nhash-format = "sha1"\n'
          f'hash = "{sha("cfmod", "sha1")}"\nmode = "metadata:curseforge"\n[update]\n[update.curseforge]\n'
          'file-id = 11\nproject-id = 22\n')
    write(pw / "mods" / "ghmod.pw.toml",
          f'name = "GH Mod"\nfilename = "ghmod.jar"\nside = "both"\n[download]\n'
          f'url = "https://github.com/me/gh/releases/download/v1/ghmod.jar"\nhash-format = "sha256"\n'
          f'hash = "{sha("ghmod", "sha256")}"\n[update]\n[update.github]\nslug = "me/gh"\n')
    write(pw / "mods" / "odd.pw.toml",
          f'name = "Odd Host"\nfilename = "odd.jar"\nside = "both"\n[download]\nurl = "https://example.com/odd.jar"\n'
          f'hash-format = "sha256"\nhash = "{sha("odd", "sha256")}"\n')
    write(pw / "mods" / "server.pw.toml", modrinth_meta("server", "server.jar", "server"))
    write(pw / "mods" / "off.pw.toml", modrinth_meta("sodium", "off.jar", "both(disabled)"))
    write(pw / "resourcepacks" / "fa.pw.toml", modrinth_meta("fa", "fa.zip", "client"))
    write(pw / "resourcepacks" / "Bundled.zip", "zip bytes")
    write(pw / "config" / "a.json", "{}")
    write(pw / "mmc-export.toml", "junk")  # Not in the index, so never shipped.
    index = ['hash-format = "sha256"']
    for rel in ("mods/sodium.pw.toml", "mods/cfmod.pw.toml", "mods/ghmod.pw.toml", "mods/odd.pw.toml",
                "mods/server.pw.toml", "mods/off.pw.toml", "resourcepacks/fa.pw.toml"):
        index.append(f'[[files]]\nfile = "{rel}"\nhash = "x"\nmetafile = true')
    for rel in ("resourcepacks/Bundled.zip", "config/a.json"):
        index.append(f'[[files]]\nfile = "{rel}"\nhash = "x"')
    write(pw / "index.toml", "\n\n".join(index) + "\n")
    write(root / "Server Pack" / "start.bat", "java -jar server.jar")
    write(root / "Server Pack" / "mods" / "patched.jar", "patched")

    monkeypatch.setattr(exporter, "CACHE_DIR", tmp_path / "cache")
    urls = {
        "https://cdn.modrinth.com/data/Psodium/versions/Vsodium/sodium.jar": JAR["sodium"],
        "https://cdn.modrinth.com/data/Pserver/versions/Vserver/server.jar": JAR["server"],
        "https://cdn.modrinth.com/data/Pfa/versions/Vfa/fa.zip": JAR["fa"],
        "https://github.com/me/gh/releases/download/v1/ghmod.jar": JAR["ghmod"],
        "https://example.com/odd.jar": JAR["odd"],
        "https://edge.forgecdn.net/cfmod.jar": JAR["cfmod"],
    }
    downloads = []

    def fake_download(url):
        downloads.append(url)
        return urls[url]

    monkeypatch.setattr(platforms, "download", fake_download)
    monkeypatch.setattr(platforms, "curseforge_files",
                        lambda ids: {11: {"id": 11, "downloadUrl": "https://edge.forgecdn.net/cfmod.jar"}})
    monkeypatch.setattr(platforms, "modrinth_versions", lambda ids: {
        f"V{name}": {"id": f"V{name}", "files": [{
            "filename": f"{name}.jar", "size": len(JAR[name]),
            "hashes": {"sha1": sha(name, "sha1"), "sha512": sha(name, "sha512")}}]}
        for name in ("sodium", "server")})
    # Sodium and the resource pack also exist on CurseForge; nothing else does.
    fingerprints = {platforms.murmur2(JAR["sodium"]): (100, 1000), platforms.murmur2(JAR["fa"]): (200, 2000)}
    monkeypatch.setattr(platforms, "curseforge_fingerprint_matches",
                        lambda fps: {fp: fingerprints[fp] for fp in fps if fp in fingerprints})
    proj, _ = project.open_project(root, project.ToolConfig())
    proj.downloads = downloads
    return proj


def entries(path):
    with zipfile.ZipFile(path) as archive:
        return set(archive.namelist())


def read(path, member):
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read(member))


def test_curseforge_pack(export_project, tmp_path):
    output = tmp_path / "cf.zip"
    contents = exporter.PackContents(export_project)
    bundled, summary = exporter.build_curseforge(export_project, contents, exporter.FileStore(tmp_path / "cache"), output)
    manifest = read(output, "manifest.json")
    assert manifest["minecraft"] == {"version": "1.21.11", "modLoaders": [{"id": "fabric-0.18.4", "primary": True}]}
    assert {(f["projectID"], f["fileID"]) for f in manifest["files"]} == {(22, 11), (100, 1000), (200, 2000)}
    assert entries(output) == {"manifest.json", "overrides/resourcepacks/Bundled.zip", "overrides/config/a.json",
                               "overrides/mods/ghmod.jar", "overrides/mods/odd.jar"}
    assert sorted(mod.slug for mod in bundled) == ["ghmod", "odd"]
    assert summary == "3 from CurseForge, 2 bundled"


def test_fingerprints_are_cached(export_project, tmp_path):
    contents = exporter.PackContents(export_project)
    exporter.build_curseforge(export_project, contents, exporter.FileStore(tmp_path / "cache"), tmp_path / "a.zip")
    first = len(export_project.downloads)
    exporter.build_curseforge(export_project, contents, exporter.FileStore(tmp_path / "cache"), tmp_path / "b.zip")
    assert len(export_project.downloads) == first  # Everything came from the cache.


def test_modrinth_pack(export_project, tmp_path):
    output = tmp_path / "pack.mrpack"
    contents = exporter.PackContents(export_project)
    bundled, summary = exporter.build_modrinth(export_project, contents, exporter.FileStore(tmp_path / "cache"), output)
    index = read(output, "modrinth.index.json")
    assert index["dependencies"] == {"minecraft": "1.21.11", "fabric-loader": "0.18.4"}
    files = {f["path"]: f for f in index["files"]}
    assert set(files) == {"mods/sodium.jar", "mods/ghmod.jar", "mods/server.jar", "resourcepacks/fa.zip"}
    assert all(set(f["hashes"]) == {"sha1", "sha512"} and f["fileSize"] for f in files.values())
    assert files["mods/sodium.jar"]["env"] == {"client": "required", "server": "unsupported"}
    assert files["mods/server.jar"]["env"] == {"client": "unsupported", "server": "required"}
    assert files["mods/ghmod.jar"]["hashes"]["sha1"] == sha("ghmod", "sha1")
    assert {"overrides/mods/cfmod.jar", "overrides/mods/odd.jar", "overrides/resourcepacks/Bundled.zip"} <= entries(output)
    assert sorted(mod.slug for mod in bundled) == ["cfmod", "odd"]
    assert summary == "4 from Modrinth, 2 bundled"


def test_server_pack(export_project, tmp_path):
    export_project.settings.server_exclude = ["GH Mod"]
    output = tmp_path / "server.zip"
    contents = exporter.PackContents(export_project)
    _, summary = exporter.build_server(export_project, contents, exporter.FileStore(tmp_path / "cache"), output)
    assert entries(output) == {"start.bat", "mods/patched.jar", "mods/cfmod.jar", "mods/odd.jar", "mods/server.jar"}
    assert summary == "3 mods + 1 from Server Pack, left out: GH Mod"


def test_bad_download_stops_the_export(export_project, tmp_path, monkeypatch):
    monkeypatch.setattr(platforms, "download", lambda url: b"tampered")
    contents = exporter.PackContents(export_project)
    with pytest.raises(ToolError, match="doesn't match the hash"):
        exporter.build_server(export_project, contents, exporter.FileStore(tmp_path / "cache"), tmp_path / "s.zip")


def test_blocked_files_are_asked_for(export_project, tmp_path, monkeypatch):
    monkeypatch.setattr(platforms, "curseforge_files", lambda ids: {11: {"id": 11, "downloadUrl": None}})
    folder = tmp_path / "instance-mods"
    write(folder / "renamed-by-user.jar", JAR["cfmod"].decode())
    answers = iter([str(folder)])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    store = exporter.FileStore(tmp_path / "cache")
    contents = exporter.PackContents(export_project)
    exporter.build_server(export_project, contents, store, tmp_path / "s.zip")
    assert "mods/cfmod.jar" in entries(tmp_path / "s.zip")
    cfmod = next(mod for mod in contents.mods if mod.slug == "cfmod")
    assert store.cached(cfmod) == JAR["cfmod"]  # Asked once, cached for next time.


def test_export_writes_named_files_and_report(export_project):
    written = exporter.export(export_project, ["curseforge", "modrinth", "server"])
    assert [path.name for path in written] == ["Pack-2.0.0.zip", "Pack-2.0.0.mrpack", "Pack-Server-2.0.0.zip"]
    report = (export_project.export_dir / "bundled_links.md").read_text(encoding="utf-8")
    assert "[GH Mod](https://github.com/me/gh): `ghmod.jar`" in report
    assert "## Modrinth pack" in report
