//go:build parity

// The parity test compares the Go diffs and drafts with the Python tool's on
// the real history of the packs. It needs the Python venv and the pack repos
// next to this one, so it only runs locally: go test -tags parity ./internal/draft
package draft

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	"github.com/HaXrDEV/Modpack-Tool/internal/diff"
	"github.com/HaXrDEV/Modpack-Tool/internal/git"
	"github.com/HaXrDEV/Modpack-Tool/internal/pack"
	"github.com/HaXrDEV/Modpack-Tool/internal/pycompat"
	"github.com/HaXrDEV/Modpack-Tool/internal/version"
)

func goHistory(t *testing.T, repo, oldRef, newRef string) any {
	ctx := context.Background()
	r := git.New(repo)
	oldTree, err := r.Snapshot(ctx, oldRef, pack.TreeParts, "Packwiz")
	if err != nil {
		t.Fatal(err)
	}
	var newTree pack.Tree
	if newRef == "WORKTREE" {
		newTree, err = pack.ReadTree(filepath.Join(repo, "Packwiz"), pack.TreeParts)
	} else {
		newTree, err = r.Snapshot(ctx, newRef, pack.TreeParts, "Packwiz")
	}
	if err != nil {
		t.Fatal(err)
	}
	packTOML, _ := pack.DecodeTOML(newTree["pack.toml"])
	d := diff.Compare(oldTree, newTree, oldRef, pycompat.Or(packTOML["version"], ""),
		pycompat.Or(pack.Table(packTOML["versions"])["minecraft"], ""), true)
	mods, _ := pack.ParseMods(newTree, pack.Categories)
	category := func(c diff.CategoryDiff) map[string]any {
		named := func(list []diff.Named) [][]string {
			result := [][]string{}
			for _, n := range list {
				result = append(result, []string{n.Name, n.Side})
			}
			return result
		}
		updated := [][]string{}
		for _, u := range c.Updated {
			updated = append(updated, []string{u.Name, u.Before, u.After})
		}
		return map[string]any{"added": named(c.Added), "removed": named(c.Removed), "updated": updated}
	}
	moved := []map[string]any{}
	for _, m := range d.Config.MovedToYOSBR {
		moved = append(moved, map[string]any{"from": m.From, "to": m.To, "content_changed": m.ContentChanged})
	}
	lineDiffs := [][]any{}
	for _, e := range d.Config.LineDiffs {
		lineDiffs = append(lineDiffs, []any{e.Path, orEmpty(e.RemovedLines), orEmpty(e.AddedLines)})
	}
	result := map[string]any{
		"summary": d.Summary(), "previous_minecraft": d.PreviousMinecraft,
		"mods": category(d.Mods), "resourcepacks": category(d.ResourcePacks), "shaderpacks": category(d.ShaderPacks),
		"newly_added": orEmpty(d.NewlyAdded), "reenabled": orEmpty(d.Reenabled),
		"config": map[string]any{"added": orEmpty(d.Config.Added), "removed": orEmpty(d.Config.Removed),
			"modified": orEmpty(d.Config.Modified), "moved": moved, "line_diffs": lineDiffs},
		"update_overview": UpdateOverview(d),
		"config_changes":  orEmpty(ConfigChanges(d, NewLabels(mods))),
	}
	return normalize(t, result)
}

func orEmpty(values []string) []string {
	if values == nil {
		return []string{}
	}
	return values
}

func normalize(t *testing.T, value any) any {
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	var result any
	json.Unmarshal(data, &result)
	return result
}

func TestParityWithPythonOnRealHistory(t *testing.T) {
	tool, _ := filepath.Abs("../..")
	python := filepath.Join(tool, "venv", "Scripts", "python.exe")
	if _, err := os.Stat(python); err != nil {
		t.Skip("no Python venv")
	}
	for _, name := range []string{"Breakneck", "Insomnia-Hardcore"} {
		repo := filepath.Join(filepath.Dir(tool), name)
		if _, err := os.Stat(filepath.Join(repo, ".git")); err != nil {
			t.Logf("skipping %s: not found", name)
			continue
		}
		tags, err := git.New(repo).Tags(context.Background())
		if err != nil {
			t.Fatal(err)
		}
		var ordered []string
		for tag := range tags {
			ordered = append(ordered, tag)
		}
		sort.Slice(ordered, func(i, j int) bool { return version.Less(ordered[i], ordered[j]) })
		var pairs [][2]string
		for i := max(1, len(ordered)-8); i < len(ordered); i++ {
			pairs = append(pairs, [2]string{ordered[i-1], ordered[i]})
		}
		pairs = append(pairs, [2]string{ordered[len(ordered)-1], "WORKTREE"})
		for _, pair := range pairs {
			cmd := exec.Command(python, filepath.Join(tool, "scripts", "golden.py"), "history", repo, pair[0], pair[1])
			out, err := cmd.Output()
			if err != nil {
				t.Fatalf("python %v: %v", pair, err)
			}
			var want any
			if err := json.Unmarshal(out, &want); err != nil {
				t.Fatal(err)
			}
			got := goHistory(t, repo, pair[0], pair[1])
			if !reflect.DeepEqual(got, want) {
				gotJSON, _ := json.MarshalIndent(got, "", " ")
				wantJSON, _ := json.MarshalIndent(want, "", " ")
				t.Errorf("%s %s -> %s differs\n%s", name, pair[0], pair[1], firstDifference(string(gotJSON), string(wantJSON)))
			} else {
				t.Logf("%s %s -> %s: same (%s)", name, pair[0], pair[1], want.(map[string]any)["summary"])
			}
		}
	}
}

func firstDifference(got, want string) string {
	g, w := strings.Split(got, "\n"), strings.Split(want, "\n")
	for i := 0; i < len(g) && i < len(w); i++ {
		if g[i] != w[i] {
			start := max(0, i-3)
			return "go:\n" + strings.Join(g[start:min(len(g), i+4)], "\n") + "\npython:\n" + strings.Join(w[start:min(len(w), i+4)], "\n")
		}
	}
	return "lengths differ"
}
