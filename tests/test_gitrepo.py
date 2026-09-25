from modpack_tool.gitrepo import GitRepo

from .conftest import git, write


def make_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "Test")

    def commit(version, mods, tag=None):
        write(root / "Packwiz" / "pack.toml", f'name = "P"\nversion = "{version}"\n[versions]\nminecraft = "1.21"\n')
        for name in mods:
            write(root / "Packwiz" / "mods" / f"{name}.pw.toml", f'name = "{name}"\n')
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", version)
        if tag:
            git(root, "tag", tag)

    commit("1.0.0", ["a"], tag="1.0.0")
    commit("1.1.0", ["a", "b"], tag="v1.1.0")
    git(root, "tag", "not-a-release")
    commit("1.2.0", ["a", "b", "c"])
    return root


def test_previous_release_and_tags(tmp_path, have_git):
    repo = GitRepo(make_repo(tmp_path))
    assert repo.is_repo
    assert repo.tag_for("1.1.0") == "v1.1.0"
    assert repo.tag_for("1.0.0") == "1.0.0"
    assert repo.tag_for("1.2.0") is None
    assert repo.previous_release("1.2.0") == "v1.1.0"
    # A version's own tag is skipped, whether or not it has the "v" prefix.
    assert repo.previous_release("1.1.0", ref="v1.1.0") == "1.0.0"
    assert repo.previous_release("1.0.0", ref="1.0.0") is None


def test_snapshot_reads_files_at_a_tag(tmp_path, have_git):
    repo = GitRepo(make_repo(tmp_path))
    tree = repo.snapshot("v1.1.0", ("pack.toml", "mods", "config"))
    assert sorted(tree) == ["mods/a.pw.toml", "mods/b.pw.toml", "pack.toml"]
    assert b'version = "1.1.0"' in tree["pack.toml"]
    assert repo.ref_exists("v1.1.0") and not repo.ref_exists("nope")


def test_status_and_commit(tmp_path, have_git):
    root = make_repo(tmp_path)
    repo = GitRepo(root)
    assert repo.status() == []
    write(root / "Packwiz" / "mods" / "d.pw.toml", 'name = "d"\n')
    assert repo.has_changes("Packwiz")
    repo.commit_all("Add d")
    assert repo.status() == [] and repo.branch() == "main"
