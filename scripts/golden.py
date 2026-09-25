"""Write the golden test fixtures for the Go port from the Python implementation.

Run with the Python tool's venv: venv\\Scripts\\python.exe scripts\\golden.py [section ...]
The fixtures pin the Python behavior the Go code must match; they stay in the
repo as the specification after the Python code is gone. This script is
deleted at the cut-over.
"""

import json
import subprocess
import sys
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
PACKS = [REPO.parent / "Breakneck", REPO.parent / "Insomnia-Hardcore"]

SECTIONS = {}


def section(function):
    SECTIONS[function.__name__] = function
    return function


def save(package, name, data):
    """Write {name: [items]} with one item per line, so diffs stay readable."""
    path = REPO / "internal" / package / "testdata" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = []
    for key, value in data.items():
        if isinstance(value, list):
            items = ",\n  ".join(json.dumps(item, ensure_ascii=False) for item in value)
            parts.append(f"{json.dumps(key)}: [\n  {items}\n ]")
        else:
            parts.append(f"{json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}")
    path.write_bytes(("{\n " + ",\n ".join(parts) + "\n}\n").encode("utf-8"))
    print(f"wrote {path.relative_to(REPO)}")


def pack_tags():
    tags = set()
    for root in PACKS:
        if (root / ".git").exists():
            tags.update(subprocess.run(["git", "tag", "--list"], cwd=root, capture_output=True, text=True,
                                       check=True).stdout.split())
    return sorted(tags)


############################################################
# version

@section
def version():
    from modpack_tool import version as v

    edge = ["", "junk", " 4.11.1 ", "4.11", "4.11.0", "26.2-1", "26.2-1.0", "26.2-1.0-beta", "26.2-1.0-rc.2",
            "26.2-1.0-ALPHA.3", "26.1.1-1.2", "26.1-1.10", "1.0.dev1", "1.0.post", "1.0-0", "1.0-7", "1!2.0",
            "1.0+local.1", "V1.2", "4.1.1A", "4.1.1z", "1.0a1.dev2", "1.0.0-alpha", "1.0.0.0.1", "1..2", "1.0.",
            ".1", "26.2-", "-1.0", "x-1.0", "26.2-1.0-beta.1.2", "1.0c1", "1.0preview2", "1.0rev3", "1.0r",
            "1.0-r-5", "2024.01.15", "007.1", "1.09", "4.12.0-beta.2", "26.2-1.0_beta1", "1.2.3-rc", "dev",
            "1.0.0-beta-2", "26.2-1.6", "1.21.11", "26", "26.1.1", "1.21", "4.1.1b", "v4.0.0_pre1"]
    versions = list(dict.fromkeys(pack_tags() + [t[1:] for t in pack_tags() if t.startswith("v")] + edge))
    targets = ["26.1", "26.1.1", "26.2", "1.21.11"]
    cases = []
    for text in versions:
        key = v.parse_pack_version_key(text)
        cases.append({
            "version": text,
            "key": [key[0], list(key[1]), list(key[2]), list(key[3]), key[4], key[5]],
            "prerelease": v.is_prerelease(text),
            "mc_prefixed": v.is_mc_prefixed_version(text),
            "anchor": v.format_version_anchor(text),
            "next_release": v.suggest_next_release(text) or "",
            "next_version": v.suggest_next_version(text) or "",
            "minor_version": v.suggest_minor_version(text) or "",
            "content_key": v.minecraft_content_key(text),
            "migration": {target: v.suggest_migration_version(target, text) for target in targets},
        })
    ordered = sorted(versions, key=v.parse_pack_version_key)
    save("version", "golden.json", {"cases": cases, "sorted": ordered})


############################################################
# pycompat

@section
def pycompat():
    values = [
        {"pack": "Breakneck", "version": "4.11.1", "loader": {"name": "Fabric", "version": "0.18.4"},
         "released": "2026-09-25", "prerelease": False, "comparedTo": None, "overview": [], "n": 5},
        {"text": "Añadido 'Sodium' — ünïcödé ✓ 🎉", "quote": 'say "hi" \\ back', "ctl": "\x00\x01\x1f\x7f\x80",
         "ws": "tab\there\nnew\rret\x08\x0c", "sep": "  ", "html": "<b>&amp;</b>"},
        [], {}, [[], {}, [1, [2, {}]]], "plain", 12345678901234, True, None,
        {"modpackVersion": "1.1.0", "projectID": 1, "modpackName": "MyPack", "useMetadata": True},
        ["a-1.jar", "b-1.jar"],
    ]
    dumps = []
    for value in values:
        for indent in (None, 1, 2):
            for ascii_only in (True, False):
                dumps.append({"input": json.dumps(value), "indent": -1 if indent is None else indent,
                              "ascii": ascii_only,
                              "output": json.dumps(value, indent=indent, ensure_ascii=ascii_only)})
    texts = ["", "a", "a\n", "a\nb", "a\r\nb\rc\n\nd", "\n", "x\x0by\x0cz\x1cw\x1d\x1e\x85q r s",
             "trailing\r\n", "  both  ", "\t\x1c\x1f text \xa0　", "\x1bnot space\x1b"]
    lines = [{"text": text, "splitlines": text.splitlines(), "strip": text.strip(), "split": text.split()}
             for text in texts]
    names = ["a/b.tar.gz", ".json", "name.", "a/.hidden", "plain", "dir/", "x.JSON", "a.b.c", ""]
    paths = [{"path": name, "suffix": PurePosixPath(name).suffix, "stem": PurePosixPath(name).stem}
             for name in names]
    words = ["hello", "HELLO wORLD", "ǆemal", "", "1abc", "élan"]
    capitalized = [{"text": word, "capitalize": word.capitalize()} for word in words]
    save("pycompat", "golden.json", {"dumps": dumps, "lines": lines, "paths": paths,
                                     "capitalize": capitalized})


############################################################
# pack: tomlkit edits of real metafiles and pack.toml

MODRINTH_VERSION = {"id": "NEWID", "files": [
    {"primary": False, "url": "https://cdn.modrinth.com/data/x/versions/y/other.jar", "filename": "other.jar",
     "hashes": {"sha512": "o"}},
    {"primary": True, "url": "https://cdn.modrinth.com/data/x/versions/y/new%20file%2B1.jar",
     "filename": "new file+1.jar", "hashes": {"sha1": "s1", "sha512": "s512"}}]}


def _shape(data, prefix=""):
    keys = []
    for key, value in data.items():
        if isinstance(value, dict):
            keys += _shape(value, f"{prefix}{key}.")
        else:
            keys.append(f"{prefix}{key}")
    return keys


@section
def pack():
    import tempfile
    import tomllib

    from modpack_tool import pack as p

    samples, seen = [], {}
    for root in PACKS:
        for path in sorted((root / "Packwiz").glob("*/*.pw.toml")):
            text = path.read_bytes().decode("utf-8")
            data = tomllib.loads(text)
            shape = (tuple(_shape(data)), "\r\n" in text, text.count("\n\n"))
            if seen.get(shape, 0) < 2:
                seen[shape] = seen.get(shape, 0) + 1
                samples.append((f"{root.name}/{path.parent.name}/{path.name}", text))
    missing_side = [(name + " (no side)", "".join(line for line in text.splitlines(True)
                                                  if not line.startswith("side =")))
                    for name, text in samples[:6]]
    cases = []
    with tempfile.TemporaryDirectory() as tmp:
        pw = Path(tmp)
        (pw / "mods").mkdir()
        target = pw / "mods" / "x.pw.toml"
        for name, text in samples + missing_side:
            for newline in ("\n", "\r\n"):
                source = text.replace("\r\n", "\n").replace("\n", newline)
                ops = ["disable", "enable"]
                if "[update.modrinth]" in source:
                    ops.append("modrinth")
                for op in ops:
                    target.write_bytes(source.encode("utf-8"))
                    mod = p.Mod("mods/x.pw.toml", tomllib.loads(source))
                    if op == "modrinth":
                        version = dict(MODRINTH_VERSION)
                        p.apply_modrinth_version(pw, mod, version)
                    else:
                        p.set_disabled(pw, mod, op == "disable")
                    cases.append({"name": name, "op": op, "input": source,
                                  "output": target.read_bytes().decode("utf-8")})
        for root in PACKS:
            source = (root / "Packwiz" / "pack.toml").read_bytes().decode("utf-8")
            for newline in ("\n", "\r\n"):
                text = source.replace("\r\n", "\n").replace("\n", newline)
                (pw / "pack.toml").write_bytes(text.encode("utf-8"))
                p.set_pack_version(pw, "9.9.9-beta.1")
                cases.append({"name": f"{root.name}/pack.toml", "op": "version", "input": text,
                              "output": (pw / "pack.toml").read_bytes().decode("utf-8")})
    save("pack", "tomledits.json", {"cases": cases})


############################################################
# project: settings rendering

SETTINGS_INPUTS = [
    ("template", None),
    ("empty", ""),
    ("comments only", "# nothing here\n"),
    ("block exports", "exports:\n- curseforge\n- server\n"),
    ("flow exports with unknown", "exports: [curseforge, itch]\nalpha_updates: sometimes\n"),
    ("scalar exports", "exports: curseforge\n"),
    ("null exports", "exports:\nside_tags: no\nmc_prefixed_versions: \"yes\"\n"),
    ("plain strings", "server_template: Server Files\nalpha_updates: never\nchangelog_url: https://x/{mc}\n"),
    ("single quotes", "server_template: 'D:\\Servers\\Pack'\nalpha_updates: 'always'\nmodrinth_notes_footer: ''\n"),
    ("double quote escapes", 'server_template: "tab\\there \\"q\\" back\\\\slash"\ncurseforge_notes_footer: "a\\nb\\n"\n'),
    ("literal footers", "curseforge_notes_footer: |\n  <br>\n\n  [banner](https://x)\nmodrinth_notes_footer: |-\n  no newline\n"),
    ("keep footer", "curseforge_notes_footer: |+\n  keep\n\n"),
    ("indented literal", "curseforge_notes_footer: |\n    four spaces\n    here\n"),
    ("flow exclude", "server_exclude: [from-the-fog, Some Mod.jar]\n"),
    ("block exclude", "server_exclude:\n  - from-the-fog\n  - other\n"),
    ("exclude needing quotes", "server_exclude:\n- 'a: b'\n- '#hash'\n- 'yes'\n- '123'\n- \"x, y\"\n- plain-one\n"),
    ("flow exclude needing quotes", "server_exclude: ['a: b', '[x]', 'true', 'ok']\n"),
    ("empty exclude", "server_exclude: []\n"),
    ("scalar exclude", "server_exclude: from-the-fog\n"),
    ("bool spellings", "mc_prefixed_versions: true\nside_tags: 1\n"),
    ("bool words", "mc_prefixed_versions: on\nside_tags: off\n"),
    ("bad bool", "side_tags: maybe\n"),
    ("numbers", "server_template: 5\nchangelog_url: 1.5\n"),
    ("null strings", "server_template:\ncurseforge_notes_footer:\nmodrinth_notes_footer: ~\n"),
    ("list for string", "changelog_url: [a, b]\n"),
    ("unknown keys", "mystery_key: 1\nexports: [modrinth]\nanother: x\n"),
    ("reordered", "side_tags: True\nexports: [server]\n"),
    ("url with hash", 'changelog_url: "https://crismpack.net/x/changelogs/{mc_group}#{anchor}"\n'),
    ("plain multi-line", "curseforge_notes_footer: first line\n  continued here\n"),
    ("unicode", 'server_template: "Sérveur Pack ✓"\nserver_exclude: [modé]\n'),
]


@section
def project():
    import builtins
    import tempfile
    from dataclasses import asdict

    from modpack_tool import project as proj

    breakneck = (REPO.parent / "Breakneck" / "modpack-tool.yml").read_bytes().decode("utf-8")
    inputs = SETTINGS_INPUTS + [("breakneck real", breakneck.replace("\r\n", "\n"))]
    cases = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, text in inputs:
            for newline in ("\n", "\r\n"):
                root = Path(tmp) / f"case{len(cases)}"
                (root / "Packwiz").mkdir(parents=True)
                source = None
                if text is not None:
                    source = text.replace("\n", newline)
                    (root / "modpack-tool.yml").write_bytes(source.encode("utf-8"))
                try:
                    settings, notes = proj.load_settings(root, "Breakneck")
                    result = {"settings": asdict(settings), "notes": notes}
                except Exception as ex:  # noqa: BLE001
                    result = {"error": str(ex)}
                path = root / "modpack-tool.yml"
                cases.append({"name": name, "input": source,
                              "output": path.read_bytes().decode("utf-8") if path.exists() else None, **result})
        builtins.input = lambda prompt="": answers.pop(0) if answers else ""
        for fixture, answer in (("breakneck.yml", ""), ("insomnia.yml", "insomnia")):
            answers = [answer]
            root = Path(tmp) / fixture
            (root / "Packwiz" / "mods").mkdir(parents=True)
            (root / "Packwiz" / "mods" / "from-the-fog.pw.toml").write_text(
                'name = "From The Fog"\nfilename = "From-The-Fog-1.21-v2.0.jar"\nside = "both"\n')
            legacy = (REPO / "tests" / "fixtures" / "legacy_settings" / fixture).read_bytes()
            (root / "settings.yml").write_bytes(legacy)
            settings, notes = proj.load_settings(root, "InsomniaHardcore")
            cases.append({"name": "legacy " + fixture, "legacy": legacy.decode("utf-8"), "answer": answer,
                          "output": (root / "modpack-tool.yml").read_bytes().decode("utf-8"),
                          "settings": asdict(settings), "notes": notes})
    save("project", "settings.json", {"cases": cases})


############################################################
# changelog: section parsing, release records, release notes, drafted sections

CHANGELOG_TEXTS = [
    ("list", "Update overview:\n  - Added 'Sodium' mod.\n  - Updated mods.\n"),
    ("literal", "Config Changes: |-\n  - Changed x: [Mod]\n  - Set \"y\" to 2: [Other]\n"),
    ("string with dashes", "Bug Fixes: \"- Fixed a crash\\n- Fixed another\"\n"),
    ("mapping bullets", "Bug Fixes:\n  - Sodium: fixed flicker\n  - Lithium: faster\n  - plain\n"),
    ("mapping section", "Bug Fixes:\n  Sodium: fixed flicker\n"),
    ("scalars", "Bug Fixes:\n  - 42\n  - true\n  - 1.50\n  - 2024-01-01\n  - ~\n"),
    ("number section", "Bug Fixes: 42\n"),
    ("placeholder", "Config Changes: |-\n  - : [mod], [Client]\n  - Changed x: [Mod]\n"),
    ("empty sections", "Update overview:\nBug Fixes: ''\nConfig Changes:\n"),
    ("metadata and unknown", "Mod loader: Fabric\nDISCLAIMER: Something important\nBug Fixes:\n  - Fixed it.\n"),
    ("quoted and odd", "Update overview:\n  - 'Added ''Mod #1'' mod.'\n  - \"Mod: Addon added\"\n  - x # comment\n"),
    ("nested", "Bug Fixes:\n  - [a, b]\n  - {k: v}\n"),
]

DRAFT_TEXTS = [
    ("template", None),
    ("comments", "# Changelog for MyPack 1.2.0\n# keep me\nUpdate overview:\n  - Old line.\n\n# Section comment\n"
                 "Bug Fixes:\n  - A very long hand-written bullet that goes on and on and on past eighty columns for sure.\n"
                 "Config Changes: |-\n  - Old: [X]\n"),
    ("missing keys", "Bug Fixes:\n  - Fixed it.\n"),
    ("flow list", "Update overview: [a, b]\nConfig Changes: 'x'\n"),
    ("block at column 0", "Update overview:\n- one\n- two\nConfig Changes:\n"),
]


@section
def changelog():
    import tempfile

    from modpack_tool import changelog as cl
    from modpack_tool import diff, project
    from tests.test_diff_and_drafting import NEW, OLD

    sections = []
    records = []
    notes = []
    drafts = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for name, text in CHANGELOG_TEXTS:
            path = tmp / "c.yml"
            path.write_text(text, encoding="utf-8")
            data = cl.load_changelog(path)
            sections.append({"name": name, "text": text,
                             "lines": {key: cl.section_lines(data.get(key)) for key in data},
                             "unknown": cl.unknown_sections(data), "empty": cl.is_empty(data)})

        root = tmp / "MyPack"
        (root / "Packwiz").mkdir(parents=True)
        (root / "Packwiz" / "pack.toml").write_text(
            'name = "MyPack"\nversion = "1.2.0"\n[versions]\nfabric = "0.18.4"\nminecraft = "1.21.11"\n')
        (root / "modpack-tool.yml").write_text("exports: []\n")
        proj, _ = project.open_project(root, project.ToolConfig())
        result = diff.compare(OLD, NEW, "1.1.0", "1.2.0", "1.21.11")
        for side_tags in (False, True):
            for name, text in CHANGELOG_TEXTS[:4] + [CHANGELOG_TEXTS[9]]:
                path = tmp / "c.yml"
                path.write_text(text, encoding="utf-8")
                data = cl.load_changelog(path)
                proj.settings.side_tags = side_tags
                record = cl.build_record(proj, data, result, released="2026-09-25")
                out = cl.write_record(proj, record)
                records.append({"changelog": text, "side_tags": side_tags,
                                "json": out.read_bytes().decode("utf-8")})
        record = cl.build_record(proj, {}, None, released="2026-09-25")
        records.append({"changelog": "", "side_tags": False, "no_diff": True,
                        "json": cl.write_record(proj, record).read_bytes().decode("utf-8")})
        proj.settings.side_tags = False

        footer = "<br>\n\n[![BisectHosting Banner](https://x/bh.png)](https://bisecthosting.com/CRISM)\n"
        variants = [("1.2.0", "https://crismpack.net/mypack/changelogs/{mc_group}#{anchor}", footer, ""),
                    ("1.3.0-beta.1", "", "", "Modrinth footer"),
                    ("26.1-1.0", "https://x/{mc}/{version}#{anchor}", footer, footer),
                    ("1.2.0", "https://x/{unknown}", "", "")]
        for version, url, cf_footer, mr_footer in variants:
            for name, text in CHANGELOG_TEXTS[:4] + [("changes only", "Changes/Improvements:\n  - New menu\n"
                                                                       "Bug Fixes:\n  - Fixed crash\n")]:
                path = tmp / "c.yml"
                path.write_text(text, encoding="utf-8")
                data = cl.load_changelog(path)
                proj.version = version
                proj.settings.changelog_url = url
                proj.settings.curseforge_notes_footer = cf_footer
                proj.settings.modrinth_notes_footer = mr_footer
                for platform in ("curseforge", "modrinth"):
                    notes.append({"version": version, "url": url, "cf_footer": cf_footer, "mr_footer": mr_footer,
                                  "changelog": text, "platform": platform,
                                  "notes": cl.release_notes(proj, data, platform)})
        proj.version = "1.2.0"

        overview = ["Added 'Brand New' mod.", "Temporarily removed incompatible mod 'Sodium Extra'.",
                    "Added 'A: B' & 'C #1' mods.", "Updated mods."]
        config_lines = ["Changed \"maxActiveTasks\" from 5 to 2: [Voxy WorldGen]", "Removed config file gone.json: [Gone]"]
        from ruamel.yaml.scalarstring import LiteralScalarString
        for name, text in DRAFT_TEXTS:
            path = tmp / "d.yml"
            if text is None:
                path.unlink(missing_ok=True)
                path = cl.create_changelog(proj)
            else:
                path.write_text(text, encoding="utf-8")
            data = cl.load_changelog(path)
            data["Update overview"] = overview
            data["Config Changes"] = LiteralScalarString("\n".join(f"- {line}" for line in config_lines))
            cl.save_changelog(path, data)
            saved = cl.load_changelog(path)
            drafts.append({"name": name, "text": text, "saved": path.read_text(encoding="utf-8"),
                           "lines": {key: cl.section_lines(saved.get(key)) for key in saved}})
    save("changelog", "golden.json", {"sections": sections, "records": records, "notes": notes, "drafts": drafts,
                                      "overview": [overview], "config_lines": [config_lines]})


############################################################
# history: diffs and drafts between two states of a real pack, printed as
# JSON for the Go parity test (internal/draft/parity_test.go).

def history(repo, old_ref, new_ref):
    from modpack_tool import diff, drafting, pack
    from modpack_tool.gitrepo import GitRepo

    git = GitRepo(Path(repo))
    old_tree = git.snapshot(old_ref, pack.TREE_PARTS)
    new_tree = pack.read_tree(Path(repo) / "Packwiz") if new_ref == "WORKTREE" else git.snapshot(new_ref, pack.TREE_PARTS)
    import tomllib
    pack_toml = tomllib.loads(new_tree["pack.toml"].decode("utf-8"))
    result = diff.compare(old_tree, new_tree, old_ref, str(pack_toml.get("version", "")),
                          str(pack_toml.get("versions", {}).get("minecraft", "")), details=True)
    labels = drafting.ModLabels(pack.parse_mods(new_tree))

    def category(c):
        return {"added": [list(n) for n in c.added], "removed": [list(n) for n in c.removed],
                "updated": [list(u) for u in c.updated]}

    return {
        "summary": result.summary(),
        "previous_minecraft": result.previous_minecraft,
        "mods": category(result.mods), "resourcepacks": category(result.resourcepacks),
        "shaderpacks": category(result.shaderpacks),
        "newly_added": result.newly_added, "reenabled": result.reenabled,
        "config": {"added": result.config.added, "removed": result.config.removed,
                   "modified": result.config.modified, "moved": result.config.moved_to_yosbr,
                   "line_diffs": [[e["path"], e["removed_lines"], e["added_lines"]] for e in result.config.line_diffs]},
        "update_overview": drafting.update_overview(result),
        "config_changes": drafting.config_changes(result, labels),
    }


if __name__ == "__main__":
    if sys.argv[1:2] == ["history"]:
        sys.stdout.buffer.write(json.dumps(history(*sys.argv[2:5]), ensure_ascii=False).encode("utf-8"))
        sys.exit(0)
    for name in sys.argv[1:] or SECTIONS:
        SECTIONS[name]()
