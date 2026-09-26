package diff

import (
	"reflect"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/HaXrDEV/Modpack-Tool/internal/pack"
	"github.com/HaXrDEV/Modpack-Tool/internal/testutil"
)

type opts = testutil.MetaOptions

var Old, New = pack.Tree(testutil.OldTree()), pack.Tree(testutil.NewTree())

func tree(files map[string]string) pack.Tree { return testutil.Tree(files) }

func compare() *PackDiff { return Compare(Old, New, "1.0.0", "1.1.0", "1.21.11", true) }

// py: test_diff_and_drafting.py::test_metafile_changes
func TestMetafileChanges(t *testing.T) {
	d := compare()
	if d.PreviousMinecraft != "1.21.10" || !d.Migration() {
		t.Error("migration", d.PreviousMinecraft)
	}
	if want := []Named{{"CleanView", "client"}, {"Brand New", "client"}}; !reflect.DeepEqual(d.Mods.Added, want) {
		t.Errorf("added %v", d.Mods.Added)
	}
	if want := []Named{{"Sodium Extra", "both"}}; !reflect.DeepEqual(d.Mods.Removed, want) {
		t.Errorf("removed %v", d.Mods.Removed)
	}
	if want := []Update{{"Sodium", "sodium-1.jar", "sodium-2.jar"}}; !reflect.DeepEqual(d.Mods.Updated, want) {
		t.Errorf("updated %v", d.Mods.Updated)
	}
	if !slices.Equal(d.NewlyAdded, []string{"Brand New"}) || !slices.Equal(d.Reenabled, []string{"CleanView"}) {
		t.Error(d.NewlyAdded, d.Reenabled)
	}
	if want := []Named{{"BSL Shaders", "client"}}; !reflect.DeepEqual(d.ShaderPacks.Added, want) || len(d.ResourcePacks.Updated) > 0 {
		t.Error(d.ShaderPacks, d.ResourcePacks)
	}
}

// py: test_diff_and_drafting.py::test_side_tags_and_summary
func TestSideTagsAndSummary(t *testing.T) {
	d := compare()
	if Tagged(d.Mods.Added[0], true) != "CleanView `Client`" || Tagged(d.Mods.Removed[0], true) != "Sodium Extra" {
		t.Error("tags")
	}
	if want := "+2 mods, -1 mod, 1 updated, 1 resource/shader pack added or removed, 4 config files changed"; d.Summary() != want {
		t.Error(d.Summary())
	}
}

// py: test_diff_and_drafting.py::test_hash_only_update_is_labelled
func TestHashOnlyUpdateIsLabelled(t *testing.T) {
	old := tree(map[string]string{"mods/a.pw.toml": testutil.Metafile("A", "a.jar")})
	now := tree(map[string]string{"mods/a.pw.toml": strings.Replace(testutil.Metafile("A", "a.jar"), `hash = "ahash"`, `hash = "0123456789abcdef"`, 1)})
	if want := []Update{{"A", "a.jar (hash ahash)", "a.jar (hash 0123456789ab)"}}; !reflect.DeepEqual(Compare(old, now, "", "", "", false).Mods.Updated, want) {
		t.Error(Compare(old, now, "", "", "", false).Mods.Updated)
	}
}

// py: test_diff_and_drafting.py::test_config_changes_detects_yosbr_moves_and_ignores_generated_files
func TestConfigDetectsYOSBRMoves(t *testing.T) {
	c := compare().Config
	if want := []Move{{"breakneckmenu.json5", "yosbr/config/breakneckmenu.json5", true}}; !reflect.DeepEqual(c.MovedToYOSBR, want) {
		t.Error(c.MovedToYOSBR)
	}
	if !slices.Equal(c.Removed, []string{"gone.json"}) || !slices.Equal(c.Added, []string{"added.json"}) {
		t.Error(c.Removed, c.Added)
	}
	if want := []string{"rpo.json", "voxy.json", "yosbr/config/breakneckmenu.json5"}; !slices.Equal(c.Modified, want) {
		t.Error(c.Modified)
	}
}

// py: test_robustness.py::test_config_diff_is_fast_on_big_rewrites
func TestConfigDiffIsFastOnBigRewrites(t *testing.T) {
	var oldLines, newLines []string
	for i := 0; i < 3000; i++ {
		oldLines = append(oldLines, `"key`+strconv.Itoa(i)+`": `+strconv.Itoa(i)+`,`)
		value := i
		if i%2 == 0 {
			value = i + 1
		}
		newLines = append(newLines, `"key`+strconv.Itoa(i)+`": `+strconv.Itoa(value)+`,`)
	}
	old := map[string][]byte{"big.json": []byte(strings.Join(oldLines, "\n"))}
	now := map[string][]byte{"big.json": []byte(strings.Join(newLines, "\n"))}
	start := time.Now()
	result := Config(old, now, true)
	if time.Since(start) > 5*time.Second {
		t.Error("too slow")
	}
	if got := result.LineDiffs[0].RemovedLines[:2]; !slices.Equal(got, []string{`"key0": 0,`, `"key2": 2,`}) {
		t.Error(got)
	}
	summary := Config(old, now, false)
	if !slices.Equal(summary.Modified, []string{"big.json"}) || len(summary.LineDiffs) != 0 {
		t.Error(summary)
	}
}
