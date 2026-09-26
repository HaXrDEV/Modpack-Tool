// Package git is a pack repo's history: release tags, the files of earlier
// releases, and the commits a publish makes.
//
// A release is a tag named after the pack version (optionally with a "v"
// prefix), so the files of any earlier release can be read straight out of git.
package git

import (
	"archive/tar"
	"bytes"
	"context"
	"errors"
	"io"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/HaXrDEV/Modpack-Tool/internal/fail"
	"github.com/HaXrDEV/Modpack-Tool/internal/files"
	"github.com/HaXrDEV/Modpack-Tool/internal/pack"
	"github.com/HaXrDEV/Modpack-Tool/internal/proc"
)

// Only version-like tags count as releases.
var releaseTagPatterns = []string{"[0-9]*", "v[0-9]*"}

// Repo is a git repository.
type Repo struct {
	Root string
	Log  func(line string) // Output of commands that change the repo.

	mu        sync.Mutex
	tags      map[string]bool
	snapshots map[string]pack.Tree
}

// New returns the repo at root.
func New(root string) *Repo {
	return &Repo{Root: root, snapshots: map[string]pack.Tree{}}
}

// IsRepo reports whether the folder is a git repository.
func (r *Repo) IsRepo() bool {
	return files.Exists(filepath.Join(r.Root, ".git"))
}

type options struct {
	changes bool
	timeout time.Duration
}

// run runs git and returns the result; failures are errors unless allowFail.
func (r *Repo) run(ctx context.Context, opts options, args ...string) (proc.Result, error) {
	if opts.timeout == 0 {
		opts.timeout = 2 * time.Minute
	}
	spec := proc.Spec{Name: "git", Args: args, Dir: r.Root, Changes: opts.changes, Timeout: opts.timeout}
	if opts.changes {
		spec.Output = func(line string) {
			if r.Log != nil && strings.TrimSpace(line) != "" {
				r.Log(line)
			}
		}
	}
	result, err := proc.Run(ctx, spec)
	switch {
	case errors.Is(err, proc.ErrNotFound):
		return result, fail.Errorf("git is not installed or not on PATH.")
	case errors.Is(err, context.DeadlineExceeded):
		return result, fail.Errorf("'git %s' timed out.", strings.Join(args, " "))
	}
	return result, err
}

// output runs a read-only git command and returns its stdout.
func (r *Repo) output(ctx context.Context, args ...string) (string, error) {
	result, err := r.run(ctx, options{}, args...)
	if err != nil {
		return "", err
	}
	if result.Code != 0 {
		return "", fail.Errorf("'git %s' failed: %s", strings.Join(args, " "), strings.TrimSpace(string(result.Stderr)))
	}
	return strings.ToValidUTF8(string(result.Stdout), "�"), nil
}

// FetchTags fetches release tags from the remote; false when that isn't possible.
func (r *Repo) FetchTags(ctx context.Context) bool {
	result, err := r.run(ctx, options{timeout: 30 * time.Second}, "fetch", "--tags", "--quiet")
	r.mu.Lock()
	r.tags = nil
	r.mu.Unlock()
	return err == nil && result.Code == 0
}

// Tags returns the repo's tags.
func (r *Repo) Tags(ctx context.Context) (map[string]bool, error) {
	r.mu.Lock()
	cached := r.tags
	r.mu.Unlock()
	if cached != nil {
		return cached, nil
	}
	out, err := r.output(ctx, "tag", "--list")
	if err != nil {
		return nil, err
	}
	tags := map[string]bool{}
	for _, tag := range strings.Fields(out) {
		tags[tag] = true
	}
	r.mu.Lock()
	r.tags = tags
	r.mu.Unlock()
	return tags, nil
}

// TagFor returns the tag of a released version ("4.11.1" or "v4.11.1"), or "".
func (r *Repo) TagFor(ctx context.Context, version string) (string, error) {
	tags, err := r.Tags(ctx)
	if err != nil {
		return "", err
	}
	for _, candidate := range []string{version, "v" + version} {
		if tags[candidate] {
			return candidate, nil
		}
	}
	return "", nil
}

// PreviousRelease is the newest release tag reachable from ref, ignoring the
// current version's own tag; "" when there is none.
func (r *Repo) PreviousRelease(ctx context.Context, currentVersion, ref string) (string, error) {
	args := []string{"describe", "--tags", "--abbrev=0"}
	for _, pattern := range releaseTagPatterns {
		args = append(args, "--match", pattern)
	}
	for _, tag := range []string{currentVersion, "v" + currentVersion} {
		args = append(args, "--exclude", tag)
	}
	result, err := r.run(ctx, options{}, append(args, ref)...)
	if err != nil || result.Code != 0 {
		return "", err
	}
	return strings.TrimSpace(string(result.Stdout)), nil
}

// RefExists reports whether ref names a commit.
func (r *Repo) RefExists(ctx context.Context, ref string) (bool, error) {
	result, err := r.run(ctx, options{}, "rev-parse", "--verify", "--quiet", ref+"^{commit}")
	return err == nil && result.Code == 0, err
}

// Snapshot returns the files under prefix/<part> at ref, as {path relative
// to prefix: bytes}, read with git archive.
func (r *Repo) Snapshot(ctx context.Context, ref string, parts []string, prefix string) (pack.Tree, error) {
	key := ref + "\x00" + strings.Join(parts, "\x00") + "\x00" + prefix
	r.mu.Lock()
	cached, ok := r.snapshots[key]
	r.mu.Unlock()
	if ok {
		return cached, nil
	}
	listed, err := r.output(ctx, "ls-tree", "--name-only", ref, prefix+"/")
	if err != nil {
		return nil, err
	}
	present := map[string]bool{}
	for _, line := range strings.Split(listed, "\n") {
		present[line] = true
	}
	var paths []string
	for _, part := range parts {
		if present[prefix+"/"+part] {
			paths = append(paths, prefix+"/"+part)
		}
	}
	tree := pack.Tree{}
	if len(paths) > 0 {
		result, err := r.run(ctx, options{}, append([]string{"archive", "--format=tar", ref, "--"}, paths...)...)
		if err != nil {
			return nil, err
		}
		if result.Code != 0 {
			return nil, fail.Errorf("'git archive %s' failed: %s", ref, strings.TrimSpace(string(result.Stderr)))
		}
		reader := tar.NewReader(bytes.NewReader(result.Stdout))
		for {
			header, err := reader.Next()
			if err == io.EOF {
				break
			} else if err != nil {
				return nil, err
			}
			// Skips the pax header with the commit id, folders and links.
			if header.Typeflag != tar.TypeReg || !strings.HasPrefix(header.Name, prefix+"/") {
				continue
			}
			rel := header.Name[len(prefix)+1:]
			if !pack.InTree(rel) {
				continue
			}
			data := make([]byte, header.Size)
			if _, err := io.ReadFull(reader, data); err != nil {
				return nil, err
			}
			tree[rel] = data
		}
	}
	r.mu.Lock()
	r.snapshots[key] = tree
	r.mu.Unlock()
	return tree, nil
}

// Branch is the current branch ("" when detached).
func (r *Repo) Branch(ctx context.Context) (string, error) {
	out, err := r.output(ctx, "branch", "--show-current")
	return strings.TrimSpace(out), err
}

// GitDir is the repository's .git folder (absolute).
func (r *Repo) GitDir(ctx context.Context) (string, error) {
	out, err := r.output(ctx, "rev-parse", "--git-dir")
	if err != nil {
		return "", err
	}
	dir := strings.TrimSpace(out)
	if !filepath.IsAbs(dir) {
		dir = filepath.Join(r.Root, dir)
	}
	return dir, nil
}

// Status returns the changed paths as `git status --porcelain` lines.
func (r *Repo) Status(ctx context.Context) ([]string, error) {
	out, err := r.output(ctx, "status", "--porcelain")
	if err != nil {
		return nil, err
	}
	var lines []string
	for _, line := range strings.Split(strings.TrimRight(out, "\n"), "\n") {
		if strings.TrimSpace(line) != "" {
			lines = append(lines, line)
		}
	}
	return lines, nil
}

func (r *Repo) change(ctx context.Context, timeout time.Duration, args ...string) error {
	result, err := r.run(ctx, options{changes: true, timeout: timeout}, args...)
	if err != nil {
		return err
	}
	if result.Code != 0 {
		return fail.Errorf("'git %s' failed: %s", strings.Join(args, " "), strings.TrimSpace(strings.Join(result.Lines, "\n")))
	}
	return nil
}

// CommitAll stages everything and commits it.
func (r *Repo) CommitAll(ctx context.Context, message string) error {
	if err := r.change(ctx, 0, "add", "--all"); err != nil {
		return err
	}
	return r.change(ctx, 0, "commit", "--message", message)
}

// Push pushes the current branch, setting its upstream on the first push.
func (r *Repo) Push(ctx context.Context) error {
	result, err := r.run(ctx, options{}, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
	if err != nil {
		return err
	}
	if result.Code == 0 {
		return r.change(ctx, 5*time.Minute, "push")
	}
	branch, err := r.Branch(ctx)
	if err != nil {
		return err
	}
	return r.change(ctx, 5*time.Minute, "push", "--set-upstream", "origin", branch)
}
