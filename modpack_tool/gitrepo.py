"""Git access for a pack repo: release tags, earlier releases' files, and publishing commits.

Release history comes from the repo itself. A release is a tag named after the
pack version (optionally with a "v" prefix), so the files of any earlier
release can be read straight out of git.
"""

import io
import subprocess
import tarfile
from pathlib import Path

from .ui import ToolError

# Only version-like tags count as releases.
_RELEASE_TAG_PATTERNS = ("[0-9]*", "v[0-9]*")


class GitRepo:
    def __init__(self, root):
        self.root = Path(root)
        self._tags = None
        self._snapshots = {}

    def run(self, *args, check=True, binary=False, timeout=120):
        """Run a git command in the repo; returns stdout (or the result when check=False)."""
        options = {} if binary else {"text": True, "encoding": "utf-8", "errors": "replace"}
        try:
            result = subprocess.run(["git", *args], cwd=self.root, capture_output=True,
                                    stdin=subprocess.DEVNULL, timeout=timeout, **options)
        except FileNotFoundError as ex:
            raise ToolError("git is not installed or not on PATH.") from ex
        except subprocess.TimeoutExpired as ex:
            raise ToolError(f"'git {' '.join(args)}' timed out.") from ex
        if not check:
            return result
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace") if binary else result.stderr
            raise ToolError(f"'git {' '.join(args)}' failed: {stderr.strip()}")
        return result.stdout

    @property
    def is_repo(self):
        return (self.root / ".git").exists()

    def fetch_tags(self):
        """Fetch release tags from the remote; returns False when that isn't possible."""
        try:
            result = self.run("fetch", "--tags", "--quiet", check=False, timeout=30)
        except ToolError:
            return False
        self._tags = None
        return result.returncode == 0

    def tags(self):
        if self._tags is None:
            self._tags = set(self.run("tag", "--list").split())
        return self._tags

    def tag_for(self, version):
        """The tag of a released version, or None when it was never tagged."""
        for candidate in (str(version), f"v{version}"):
            if candidate in self.tags():
                return candidate
        return None

    def previous_release(self, current_version, ref="HEAD"):
        """The newest release tag reachable from ``ref``, ignoring ``current_version``'s own tag."""
        args = ["describe", "--tags", "--abbrev=0"]
        for pattern in _RELEASE_TAG_PATTERNS:
            args += ["--match", pattern]
        for tag in (str(current_version), f"v{current_version}"):
            args += ["--exclude", tag]
        result = self.run(*args, ref, check=False)
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def ref_exists(self, ref):
        return self.run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False).returncode == 0

    def snapshot(self, ref, parts, prefix="Packwiz"):
        """Files under ``prefix/<part>`` at ``ref``, as {path relative to prefix: bytes}."""
        key = (ref, tuple(parts), prefix)
        if key not in self._snapshots:
            listed = set(self.run("ls-tree", "--name-only", ref, f"{prefix}/").split("\n"))
            paths = [f"{prefix}/{part}" for part in parts if f"{prefix}/{part}" in listed]
            tree = {}
            if paths:
                data = self.run("archive", "--format=tar", ref, "--", *paths, binary=True)
                with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                    for member in archive.getmembers():
                        if member.isfile():
                            tree[member.name[len(prefix) + 1:]] = archive.extractfile(member).read()
            self._snapshots[key] = tree
        return self._snapshots[key]

    def branch(self):
        return self.run("branch", "--show-current").strip()

    def status(self):
        """Changed paths as `git status --porcelain` lines."""
        return [line for line in self.run("status", "--porcelain").splitlines() if line.strip()]

    def has_changes(self, path):
        return bool(self.run("status", "--porcelain", "--", path).strip())

    def commit_all(self, message):
        self.run("add", "--all")
        self.run("commit", "--message", message)

    def push(self):
        upstream = self.run("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False)
        if upstream.returncode == 0:
            self.run("push", timeout=300)
        else:
            self.run("push", "--set-upstream", "origin", self.branch(), timeout=300)
