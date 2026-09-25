from modpack_tool import diff, drafting, pack

from .conftest import metafile


def tree(files):
    return {path: text.encode() for path, text in files.items()}


OLD = tree({
    "pack.toml": '[versions]\nminecraft = "1.21.10"\n',
    "mods/sodium.pw.toml": metafile("Sodium", "sodium-1.jar", side="client"),
    "mods/lithium.pw.toml": metafile("Lithium", "lithium-1.jar"),
    "mods/sodium-extra.pw.toml": metafile("Sodium Extra", "extra-1.jar"),
    "mods/cleanview.pw.toml": metafile("CleanView", "cleanview.jar", side="client(disabled)"),
    "mods/switch.pw.toml": metafile("Switcher", "switch-1.jar", source="curseforge"),
    "mods/renamed-old.pw.toml": metafile("Renamed", "renamed-1.jar"),
    "resourcepacks/fa.pw.toml": metafile("Fresh Animations", "fa-1.zip", side="client"),
    "config/bcc.json": '{"modpackVersion": "1.0"}',
    "config/crash_assistant/modlist.json": "[]",
    "config/breakneckmenu.json5": '{\n  coloredText: false\n}\n',
    "config/voxy.json": '{\n  "maxActiveTasks": 5,\n  "enabled": true\n}\n',
    "config/gone.json": "{}",
    "config/rpo.json": '{\n  "default_packs": [\n    "file/A.zip",\n    "file/B.zip"\n  ]\n}\n',
})

NEW = tree({
    "pack.toml": '[versions]\nminecraft = "1.21.11"\n',
    "mods/sodium.pw.toml": metafile("Sodium", "sodium-2.jar", side="client"),
    "mods/lithium.pw.toml": metafile("Lithium", "lithium-1.jar"),
    "mods/cleanview.pw.toml": metafile("CleanView", "cleanview.jar", side="client"),
    "mods/new.pw.toml": metafile("Brand New [Fabric]", "new-1.jar", side="client"),
    # Same jar, now tracked through Modrinth instead of CurseForge: not an update.
    "mods/switch.pw.toml": metafile("Switcher", "switch-1.jar", source="modrinth"),
    "mods/renamed-new.pw.toml": metafile("Renamed", "renamed-1.jar"),
    "resourcepacks/fa.pw.toml": metafile("Fresh Animations", "fa-1.zip", side="client"),
    "shaderpacks/bsl.pw.toml": metafile("BSL Shaders", "bsl.zip", side="client"),
    "config/bcc.json": '{"modpackVersion": "2.0"}',
    "config/crash_assistant/modlist.json": '["x"]',
    "config/yosbr/config/breakneckmenu.json5": '{\n  coloredText: true\n}\n',
    "config/voxy.json": '{\n  "maxActiveTasks": 2,\n  "enabled": true\n}\n',
    "config/rpo.json": '{\n  "default_packs": [\n    "file/B.zip",\n    "file/A.zip",\n    "file/C.zip"\n  ]\n}\n',
    "config/added.json": "{}",
})


def compare():
    return diff.compare(OLD, NEW, "1.0.0", "1.1.0", "1.21.11")


def test_metafile_changes():
    result = compare()
    assert result.previous_minecraft == "1.21.10" and result.migration
    assert result.mods.added == [diff.Named("CleanView", "client"), diff.Named("Brand New", "client")]
    assert result.mods.removed == [diff.Named("Sodium Extra", "both")]
    assert result.mods.updated == [("Sodium", "sodium-1.jar", "sodium-2.jar")]
    assert result.newly_added == ["Brand New"]
    assert result.reenabled == ["CleanView"]
    assert result.shaderpacks.added == [diff.Named("BSL Shaders", "client")]
    assert not result.resourcepacks.updated


def test_side_tags_and_summary():
    result = compare()
    assert diff.tagged(result.mods.added[0], True) == "CleanView `Client`"
    assert diff.tagged(result.mods.removed[0], True) == "Sodium Extra"
    assert result.summary() == "+2 mods, -1 mod, 1 updated, 1 resource/shader pack added or removed, 4 config files changed"


def test_hash_only_update_is_labelled():
    old = tree({"mods/a.pw.toml": metafile("A", "a.jar")})
    new = tree({"mods/a.pw.toml": metafile("A", "a.jar").replace('hash = "ahash"', 'hash = "0123456789abcdef"')})
    assert diff.diff_category(old, new, "mods").updated == [("A", "a.jar (hash ahash)", "a.jar (hash 0123456789ab)")]


def test_config_changes_detects_yosbr_moves_and_ignores_generated_files():
    config = compare().config
    assert config.moved_to_yosbr == [{"from": "breakneckmenu.json5", "to": "yosbr/config/breakneckmenu.json5",
                                      "content_changed": True}]
    assert config.removed == ["gone.json"]
    assert config.added == ["added.json"]
    assert config.modified == ["rpo.json", "voxy.json", "yosbr/config/breakneckmenu.json5"]


def test_update_overview_uses_real_names():
    lines = drafting.update_overview(compare())
    assert lines == [
        "Updated to Minecraft 1.21.11.",
        "Added 'Brand New' mod.",
        "Re-added some mods.",
        "Temporarily removed incompatible mod 'Sodium Extra'.",  # Not mistaken for "Sodium".
        "Updated mods.",
        "Added 'BSL Shaders' shaderpack.",
    ]


def test_update_overview_without_migration():
    result = compare()
    result.previous_minecraft = "1.21.11"
    lines = drafting.update_overview(result)
    assert "Removed 'Sodium Extra' mod." in lines
    empty = diff.compare(OLD, OLD, "1.0.0", "1.0.1", "1.21.10")
    assert drafting.update_overview(empty) == ["Maintenance update."]


def test_config_change_draft():
    result = compare()
    labels = drafting.ModLabels(pack.parse_mods(NEW) + [pack.Mod("mods/voxy.pw.toml", {"name": "Voxy WorldGen"}),
                                                     pack.Mod("mods/breakneckmenu.pw.toml", {"name": "Breakneck Menu"}),
                                                     pack.Mod("mods/rpo.pw.toml", {"name": "Resource Pack Overrides"})])
    assert drafting.config_changes(result, labels) == [
        "- Moved breakneckmenu.json5 to YOSBR so it applies as a default on first launch: [Breakneck Menu]",
        "- Removed config file gone.json: [Gone]",
        "- Reordered B.zip in section default_packs: [Resource Pack Overrides]",
        "- Added C.zip to section default_packs: [Resource Pack Overrides]",
        '- Changed "maxActiveTasks" from 5 to 2: [Voxy WorldGen]',
        '- Changed default "coloredText" from false to true: [Breakneck Menu]',
    ]


def test_labels_match_config_paths_to_mods():
    labels = drafting.ModLabels([
        pack.Mod("mods/voxy-worldgen.pw.toml", {"name": "Voxy WorldGen"}),
        pack.Mod("mods/sodium.pw.toml", {"name": "Sodium"}),
        pack.Mod("mods/lambdynamiclights.pw.toml", {"name": "LambDynamicLights - Dynamic Lights"}),
        pack.Mod("mods/off.pw.toml", {"name": "Disabled One", "side": "both(disabled)"}),
    ])
    assert labels.for_config("yosbr/config/voxyworldgenv2.json") == "Voxy WorldGen"
    assert labels.for_config("sodium-options.json") == "Sodium"
    assert labels.for_config("fancymenu/customization/x.txt") == "Fancymenu"
    assert labels.for_config("disabled_one.json") == "Disabled One"
