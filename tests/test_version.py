from modpack_tool.version import (
    format_version_anchor,
    is_mc_prefixed_version,
    is_prerelease,
    minecraft_content_key,
    parse_pack_version_key,
    suggest_migration_version,
    suggest_minor_version,
    suggest_next_release,
    suggest_next_version,
)


def test_ordering_mixes_both_schemes_and_never_raises():
    versions = ["4.1.1a", "4.1.1", "4.11.0-beta.1", "4.11.0", "4.2.0", "junk", "",
                "26.1-1.0-beta.1", "26.1-1.0", "26.1-1.10", "26.1-1.2"]
    ordered = sorted(versions, key=parse_pack_version_key)
    assert ordered == ["", "junk", "4.1.1", "4.1.1a", "4.2.0", "4.11.0-beta.1", "4.11.0",
                       "26.1-1.0-beta.1", "26.1-1.0", "26.1-1.2", "26.1-1.10"]


def test_prerelease_detection():
    assert is_prerelease("4.11.0-beta.1")
    assert is_prerelease("26.2-1.0-rc.2")
    assert is_prerelease("2.0.0.pre6")  # PEP 440 spelling used by old Insomnia tags
    assert not is_prerelease("4.11.1")
    assert not is_prerelease("26.2-1.0")


def test_scheme_detection_and_anchor():
    assert is_mc_prefixed_version("26.1.1-1.2")
    assert not is_mc_prefixed_version("4.11.1")
    assert format_version_anchor("4.11.1") == "v4.11.1"
    assert format_version_anchor("26.1-1.0") == "26.1-1.0"


def test_content_key():
    assert minecraft_content_key("26.1.1") == "26.1"
    assert minecraft_content_key("1.21.11") == "1.21"
    assert minecraft_content_key("26") == "26"


def test_suggestions():
    assert suggest_next_release("26.2-1.6") == "26.2-1.7"
    assert suggest_next_release("26.2-1.0-beta.1") == "26.2-1.0"
    assert suggest_next_release("4.11.1") is None
    assert suggest_next_version("4.11.1") == "4.11.2"
    assert suggest_next_version("4.12.0-beta.1") == "4.12.0"
    assert suggest_next_version("4.1.1a") == "4.1.2"
    assert suggest_next_version("26.2-1.6") == "26.2-1.7"
    assert suggest_next_version("junk") is None
    assert suggest_minor_version("4.11.1") == "4.12.0"
    assert suggest_minor_version("4.12.0-beta.2") == "4.13.0"
    assert suggest_minor_version("26.2-1.6") is None
    assert suggest_migration_version("26.1", "4.11.1") == "26.1-1.0"
    assert suggest_migration_version("26.1.1", "26.1-1.3") == "26.1.1-1.4"
    assert suggest_migration_version("26.2", "26.1.1-1.4") == "26.2-1.0"
