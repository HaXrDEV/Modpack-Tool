package proc

import (
	"context"
	"os/exec"
	"slices"
	"testing"
)

func TestCleanLine(t *testing.T) {
	cases := map[string]string{
		"plain":                                 "plain",
		"\x1b[1A\x1b[JRefreshing index... 45 %": "Refreshing index... 45 %",
		"a 10%\rb 20%\rdone   ":                 "done",
		"crlf\r":                                "crlf",
		"bad \xff byte":                         "bad � byte",
	}
	for in, want := range cases {
		if got := CleanLine(in); got != want {
			t.Errorf("CleanLine(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestRunStreamsLines(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git is not installed")
	}
	var lines []string
	result, err := Run(context.Background(), Spec{Name: "git", Args: []string{"--version"}, Output: func(line string) {
		lines = append(lines, line)
	}})
	if err != nil || result.Code != 0 || len(lines) != 1 || !slices.Equal(lines, result.Lines) {
		t.Errorf("%v %v %q", result, err, lines)
	}
	if _, err := Run(context.Background(), Spec{Name: "surely-not-a-command-xyz"}); err != ErrNotFound {
		t.Errorf("missing command: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := Run(ctx, Spec{Name: "git", Args: []string{"--version"}}); err != context.Canceled {
		t.Errorf("canceled: %v", err)
	}
}
