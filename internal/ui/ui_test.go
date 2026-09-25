package ui

import (
	"bytes"
	"context"
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

func TestParseNumbers(t *testing.T) {
	cases := map[string][]int{"1": {0}, "1-": {0}, "1,3-5": {0, 2, 3, 4}, " 2 , 2-3 ": {1, 2}, "5-5": {4}, ",1,": {0}}
	for text, want := range cases {
		if got, ok := ParseNumbers(text, 5); !ok || !slices.Equal(got, want) {
			t.Errorf("ParseNumbers(%q) = %v, %v", text, got, ok)
		}
	}
	for _, bad := range []string{"0", "6", "3-2", "a", "-2", "1-x", "+1"} {
		if _, ok := ParseNumbers(bad, 5); ok {
			t.Errorf("ParseNumbers(%q) should fail", bad)
		}
	}
}

func TestPlainPrompts(t *testing.T) {
	ctx := context.Background()
	var out bytes.Buffer
	s := NewPlain(strings.NewReader("maybe\ny\n\nx\nr\n2,4\n\nall\n\n"), &out)
	if ok, err := s.Confirm(ctx, "Go?", false); !ok || err != nil {
		t.Error("confirm y", ok, err)
	}
	if ok, _ := s.Confirm(ctx, "Go?", true); !ok {
		t.Error("confirm default")
	}
	if key, _ := s.Choose(ctx, "Which?", []Option{{"r", "rename"}, {"n", "new"}}, "n"); key != "r" {
		t.Error("choose", key)
	}
	if picked, _ := s.PickMany(ctx, "Pick?", []string{"a", "b", "c", "d"}); !slices.Equal(picked, []int{1, 3}) {
		t.Error("pick", picked)
	}
	if picked, _ := s.PickMany(ctx, "Pick?", []string{"a"}); picked != nil {
		t.Error("pick default none", picked)
	}
	if picked, _ := s.PickMany(ctx, "Pick?", []string{"a", "b"}); len(picked) != 2 {
		t.Error("pick all", picked)
	}
	if answer, _ := s.Ask(ctx, "Version", "1.0.1"); answer != "1.0.1" {
		t.Error("ask default", answer)
	}
	if _, err := s.Ask(ctx, "More?", ""); !errors.Is(err, context.Canceled) {
		t.Error("end of input should cancel", err)
	}
	text := out.String()
	for _, want := range []string{"Go? [y/N]: ", "  Please answer y or n.", "Which? (r = rename, n = new) [n]: ",
		"  Please enter one of: r, n.", "     2) b", "Pick? (all / none / numbers like 1,3-5) [none]: ", "Version [1.0.1]: "} {
		if !strings.Contains(text, want) {
			t.Errorf("output lacks %q:\n%s", want, text)
		}
	}
}

func TestCleanPath(t *testing.T) {
	os.Setenv("MODPACK_TEST_DIR", filepath.FromSlash("/tmp/x"))
	cases := map[string]string{
		`"D:\Games\My Pack"`: filepath.Clean(`D:\Games\My Pack`),
		" 'a/b/' ":           filepath.Clean("a/b"),
		"%MODPACK_TEST_DIR%": filepath.Clean(filepath.FromSlash("/tmp/x")),
		"%NOT_SET_XYZ%/y":    filepath.Clean("%NOT_SET_XYZ%/y"),
		"  ":                 "",
	}
	for in, want := range cases {
		if got := CleanPath(in); got != want {
			t.Errorf("CleanPath(%q) = %q, want %q", in, got, want)
		}
	}
}
