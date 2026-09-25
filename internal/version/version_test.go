package version

import (
	"encoding/json"
	"os"
	"reflect"
	"slices"
	"sort"
	"testing"
)

type goldenCase struct {
	Version      string            `json:"version"`
	Key          []any             `json:"key"`
	Prerelease   bool              `json:"prerelease"`
	MCPrefixed   bool              `json:"mc_prefixed"`
	Anchor       string            `json:"anchor"`
	NextRelease  string            `json:"next_release"`
	NextVersion  string            `json:"next_version"`
	MinorVersion string            `json:"minor_version"`
	ContentKey   string            `json:"content_key"`
	Migration    map[string]string `json:"migration"`
}

func ints(value any) []int {
	result := []int{}
	for _, n := range value.([]any) {
		result = append(result, int(n.(float64)))
	}
	return result
}

// The goldens come from the Python implementation (scripts/golden.py at the python-final tag) over
// every tag of both packs plus edge cases.
func TestGolden(t *testing.T) {
	data, err := os.ReadFile("testdata/golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		Cases  []goldenCase `json:"cases"`
		Sorted []string     `json:"sorted"`
	}
	if err := json.Unmarshal(data, &golden); err != nil {
		t.Fatal(err)
	}
	for _, c := range golden.Cases {
		want := Key{
			Kind: int(c.Key[0].(float64)), Main: ints(c.Key[1]), Release: ints(c.Key[2]),
			Post: int(c.Key[4].(float64)), Text: c.Key[5].(string),
		}
		pre := ints(c.Key[3])
		want.Pre = [2]int{pre[0], pre[1]}
		if got := ParseKey(c.Version); !reflect.DeepEqual(got, want) {
			t.Errorf("ParseKey(%q) = %+v, want %+v", c.Version, got, want)
		}
		checks := []struct {
			name      string
			got, want any
		}{
			{"IsPrerelease", IsPrerelease(c.Version), c.Prerelease},
			{"IsMCPrefixed", IsMCPrefixed(c.Version), c.MCPrefixed},
			{"Anchor", Anchor(c.Version), c.Anchor},
			{"NextRelease", NextRelease(c.Version), c.NextRelease},
			{"NextVersion", NextVersion(c.Version), c.NextVersion},
			{"MinorVersion", MinorVersion(c.Version), c.MinorVersion},
			{"ContentKey", ContentKey(c.Version), c.ContentKey},
		}
		for target, want := range c.Migration {
			checks = append(checks, struct {
				name      string
				got, want any
			}{"MigrationVersion(" + target + ")", MigrationVersion(target, c.Version), want})
		}
		for _, check := range checks {
			if check.got != check.want {
				t.Errorf("%s(%q) = %v, want %v", check.name, c.Version, check.got, check.want)
			}
		}
	}
	ordered := make([]string, 0, len(golden.Cases))
	for _, c := range golden.Cases {
		ordered = append(ordered, c.Version)
	}
	sort.SliceStable(ordered, func(i, j int) bool { return Less(ordered[i], ordered[j]) })
	if !slices.Equal(ordered, golden.Sorted) {
		t.Errorf("sorted order differs:\n got %q\nwant %q", ordered, golden.Sorted)
	}
}

// py: test_version.py::test_ordering_mixes_both_schemes_and_never_raises
func TestOrderingMixesBothSchemes(t *testing.T) {
	versions := []string{"4.1.1a", "4.1.1", "4.11.0-beta.1", "4.11.0", "4.2.0", "junk", "",
		"26.1-1.0-beta.1", "26.1-1.0", "26.1-1.10", "26.1-1.2"}
	sort.SliceStable(versions, func(i, j int) bool { return Less(versions[i], versions[j]) })
	want := []string{"", "junk", "4.1.1", "4.1.1a", "4.2.0", "4.11.0-beta.1", "4.11.0",
		"26.1-1.0-beta.1", "26.1-1.0", "26.1-1.2", "26.1-1.10"}
	if !slices.Equal(versions, want) {
		t.Errorf("got %q", versions)
	}
}

// py: test_version.py::test_prerelease_detection
func TestPrereleaseDetection(t *testing.T) {
	for v, want := range map[string]bool{
		"4.11.0-beta.1": true, "26.2-1.0-rc.2": true, "2.0.0.pre6": true, "4.11.1": false, "26.2-1.0": false,
	} {
		if IsPrerelease(v) != want {
			t.Errorf("IsPrerelease(%q) != %v", v, want)
		}
	}
}

// py: test_version.py::test_scheme_detection_and_anchor
func TestSchemeAndAnchor(t *testing.T) {
	if !IsMCPrefixed("26.1.1-1.2") || IsMCPrefixed("4.11.1") {
		t.Error("IsMCPrefixed")
	}
	if Anchor("4.11.1") != "v4.11.1" || Anchor("26.1-1.0") != "26.1-1.0" {
		t.Error("Anchor")
	}
}

// py: test_version.py::test_content_key
func TestContentKey(t *testing.T) {
	for mc, want := range map[string]string{"26.1.1": "26.1", "1.21.11": "1.21", "26": "26"} {
		if got := ContentKey(mc); got != want {
			t.Errorf("ContentKey(%q) = %q", mc, got)
		}
	}
}

// py: test_version.py::test_suggestions
func TestSuggestions(t *testing.T) {
	checks := [][2]string{
		{NextRelease("26.2-1.6"), "26.2-1.7"},
		{NextRelease("26.2-1.0-beta.1"), "26.2-1.0"},
		{NextRelease("4.11.1"), ""},
		{NextVersion("4.11.1"), "4.11.2"},
		{NextVersion("4.12.0-beta.1"), "4.12.0"},
		{NextVersion("4.1.1a"), "4.1.2"},
		{NextVersion("26.2-1.6"), "26.2-1.7"},
		{NextVersion("junk"), ""},
		{MinorVersion("4.11.1"), "4.12.0"},
		{MinorVersion("4.12.0-beta.2"), "4.13.0"},
		{MinorVersion("26.2-1.6"), ""},
		{MigrationVersion("26.1", "4.11.1"), "26.1-1.0"},
		{MigrationVersion("26.1.1", "26.1-1.3"), "26.1.1-1.4"},
		{MigrationVersion("26.2", "26.1.1-1.4"), "26.2-1.0"},
	}
	for i, c := range checks {
		if c[0] != c[1] {
			t.Errorf("check %d: got %q, want %q", i, c[0], c[1])
		}
	}
}
