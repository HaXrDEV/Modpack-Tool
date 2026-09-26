package proc

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
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

// A stray quote in PATH (`C:\Program Files\PowerShell\7"`) hides every later
// folder from Go's exec.LookPath; LookPath and child processes must not care.
func TestStrayQuoteInPath(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("PATH quoting is a Windows matter")
	}
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "fake-tool.exe"), []byte("MZ"), 0o755)
	t.Setenv("PATH", `C:\Program Files\PowerShell\7";`+dir+`;C:\Windows\System32`)
	if _, err := exec.LookPath("fake-tool"); err == nil {
		t.Skip("this Go version reads stray quotes like cmd.exe")
	}
	if path, err := LookPath("fake-tool"); err != nil || path != filepath.Join(dir, "fake-tool.exe") {
		t.Errorf("LookPath = %q, %v", path, err)
	}
	cleaned := cleanPath([]string{"A=1", "PATH=" + os.Getenv("PATH")})
	if want := `PATH=C:\Program Files\PowerShell\7;` + dir + `;C:\Windows\System32`; cleaned[1] != want {
		t.Errorf("cleanPath = %q", cleaned[1])
	}
	if _, err := Run(context.Background(), Spec{Name: "cmd", Args: []string{"/c", "exit 0"}}); err != nil {
		t.Errorf("cmd /c: %v", err)
	}
}

func TestRunStreamsLines(t *testing.T) {
	if _, err := LookPath("git"); err != nil {
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
