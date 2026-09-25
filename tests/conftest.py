import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def write(path, text, newline="\n"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)
    return path


def metafile(name, filename, side="both", source="modrinth", pin=False, extra=""):
    """Text of a packwiz metafile for tests."""
    lines = [f'name = "{name}"', f'filename = "{filename}"', f'side = "{side}"']
    if pin:
        lines.append("pin = true")
    if source == "modrinth":
        lines += ["", "[download]", f'url = "https://cdn.modrinth.com/data/AAA/versions/BBB/{filename}"',
                  'hash-format = "sha512"', f'hash = "{name.lower()}hash"', "", "[update]",
                  "[update.modrinth]", f'mod-id = "{name[:8]}"', f'version = "{name[:4]}v1"']
    elif source == "curseforge":
        lines += ["", "[download]", 'hash-format = "sha1"', f'hash = "{name.lower()}sha1"',
                  'mode = "metadata:curseforge"', "", "[update]", "[update.curseforge]",
                  "file-id = 111", "project-id = 222"]
    else:
        lines += ["", "[download]", f'url = "https://example.com/{filename}"', 'hash-format = "sha256"',
                  f'hash = "{name.lower()}sha256"']
    return "\n".join(lines) + "\n" + extra


@pytest.fixture
def pack_dir(tmp_path):
    """A small packwiz pack with mixed sources, a disabled mod and a pinned mod."""
    root = tmp_path / "MyPack"
    pw = root / "Packwiz"
    write(pw / "pack.toml", 'name = "MyPack"\nauthor = "me"\nversion = "1.2.0"\npack-format = "packwiz:1.1.0"\n\n'
          '[index]\nfile = "index.toml"\nhash-format = "sha256"\nhash = "abc"\n\n'
          '[versions]\nfabric = "0.18.4"\nminecraft = "1.21.11"\n')
    write(pw / "mods" / "sodium.pw.toml", metafile("Sodium", "sodium-0.8.jar", side="client"), newline="\r\n")
    write(pw / "mods" / "lithium.pw.toml", metafile("Lithium", "lithium-0.21.jar"))
    write(pw / "mods" / "boss.pw.toml", metafile("Boss Checklist", "boss-4.1.jar", side="both(disabled)",
                                                  source="curseforge"))
    write(pw / "mods" / "pinned.pw.toml", metafile("Pinned Mod [Fabric]", "pinned-1.0.jar", pin=True))
    write(pw / "mods" / "server.pw.toml", metafile("Server Thing", "server-1.0.jar", side="server", source="url"))
    write(pw / "resourcepacks" / "fresh.pw.toml", metafile("Fresh Animations", "fa.zip", side="client"))
    write(pw / "mods" / "disabled" / "old.pw.toml", metafile("Old", "old.jar"))  # Ignored subfolder.
    write(pw / "config" / "bcc.json", '{"projectID": 1, "modpackName": "MyPack", "modpackVersion": "1.1.0"}')
    write(pw / "index.toml", 'hash-format = "sha256"\n\n'
          '[[files]]\nfile = "config/bcc.json"\nhash = "x"\n\n'
          '[[files]]\nfile = "mods/sodium.pw.toml"\nhash = "y"\nmetafile = true\n')
    return pw


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def have_git():
    if not shutil.which("git"):
        pytest.skip("git is not installed")
