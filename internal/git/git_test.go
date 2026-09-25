package git

import (
	"context"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/HaXrDEV/Modpack-Tool/internal/testutil"
)

func makeRepo(t *testing.T) string {
	t.Helper()
	testutil.RequireGit(t)
	root := filepath.Join(t.TempDir(), "repo")
	testutil.Write(t, filepath.Join(root, ".keep"), "")
	testutil.Git(t, root, "init", "-q", "-b", "main")
	testutil.Git(t, root, "config", "user.email", "t@example.com")
	testutil.Git(t, root, "config", "user.name", "Test")
	commit := func(version string, mods []string, tag string) {
		testutil.Write(t, filepath.Join(root, "Packwiz", "pack.toml"), "name = \"P\"\nversion = \""+version+"\"\n[versions]\nminecraft = \"1.21\"\n")
		for _, name := range mods {
			testutil.Write(t, filepath.Join(root, "Packwiz", "mods", name+".pw.toml"), "name = \""+name+"\"\n")
		}
		testutil.Git(t, root, "add", "-A")
		testutil.Git(t, root, "commit", "-q", "-m", version)
		if tag != "" {
			testutil.Git(t, root, "tag", tag)
		}
	}
	commit("1.0.0", []string{"a"}, "1.0.0")
	commit("1.1.0", []string{"a", "b"}, "v1.1.0")
	testutil.Git(t, root, "tag", "not-a-release")
	commit("1.2.0", []string{"a", "b", "c"}, "")
	return root
}

// py: test_gitrepo.py::test_previous_release_and_tags
func TestPreviousReleaseAndTags(t *testing.T) {
	ctx := context.Background()
	repo := New(makeRepo(t))
	if !repo.IsRepo() {
		t.Fatal("not a repo")
	}
	for version, want := range map[string]string{"1.1.0": "v1.1.0", "1.0.0": "1.0.0", "1.2.0": ""} {
		if got, err := repo.TagFor(ctx, version); err != nil || got != want {
			t.Errorf("TagFor(%s) = %q, %v", version, got, err)
		}
	}
	checks := [][3]string{{"1.2.0", "HEAD", "v1.1.0"}, {"1.1.0", "v1.1.0", "1.0.0"}, {"1.0.0", "1.0.0", ""}}
	for _, c := range checks {
		// A version's own tag is skipped, with or without the "v" prefix.
		if got, err := repo.PreviousRelease(ctx, c[0], c[1]); err != nil || got != c[2] {
			t.Errorf("PreviousRelease(%s, %s) = %q, %v", c[0], c[1], got, err)
		}
	}
}

// py: test_gitrepo.py::test_snapshot_reads_files_at_a_tag
func TestSnapshotReadsFilesAtATag(t *testing.T) {
	ctx := context.Background()
	repo := New(makeRepo(t))
	tree, err := repo.Snapshot(ctx, "v1.1.0", []string{"pack.toml", "mods", "config"}, "Packwiz")
	if err != nil {
		t.Fatal(err)
	}
	var paths []string
	for path := range tree {
		paths = append(paths, path)
	}
	slices.Sort(paths)
	if !slices.Equal(paths, []string{"mods/a.pw.toml", "mods/b.pw.toml", "pack.toml"}) {
		t.Errorf("paths %q", paths)
	}
	if !strings.Contains(string(tree["pack.toml"]), `version = "1.1.0"`) {
		t.Error("pack.toml content")
	}
	if ok, _ := repo.RefExists(ctx, "v1.1.0"); !ok {
		t.Error("v1.1.0 should exist")
	}
	if ok, _ := repo.RefExists(ctx, "nope"); ok {
		t.Error("nope shouldn't exist")
	}
}

// py: test_gitrepo.py::test_status_and_commit
func TestStatusAndCommit(t *testing.T) {
	ctx := context.Background()
	root := makeRepo(t)
	repo := New(root)
	if status, err := repo.Status(ctx); err != nil || len(status) != 0 {
		t.Fatal(status, err)
	}
	testutil.Write(t, filepath.Join(root, "Packwiz", "mods", "d.pw.toml"), "name = \"d\"\n")
	if changed, _ := repo.HasChanges(ctx, "Packwiz"); !changed {
		t.Error("expected changes")
	}
	if err := repo.CommitAll(ctx, "Add d"); err != nil {
		t.Fatal(err)
	}
	status, _ := repo.Status(ctx)
	branch, _ := repo.Branch(ctx)
	if len(status) != 0 || branch != "main" {
		t.Error(status, branch)
	}
}
