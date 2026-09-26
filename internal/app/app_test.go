package app

import (
	"bytes"
	"path/filepath"
	"strings"
	"testing"

	"github.com/HaXrDEV/Modpack-Tool/internal/testutil"
	"github.com/HaXrDEV/Modpack-Tool/internal/workflow"
)

// py: test_workflows.py::test_cli_parser_lists_every_command
func TestCommandLineListsEveryCommand(t *testing.T) {
	projectPath, command, rest, err := parseGlobal([]string{"--project", "X", "build", "--skip-server", "--since", "4.11.0"})
	if err != nil || projectPath != "X" || command != "build" {
		t.Fatal(projectPath, command, err)
	}
	build, _ := workflow.ActionByName(command)
	args, err := build.ParseArgs(rest)
	if err != nil || !args.SkipServer || args.Since != "4.11.0" {
		t.Error(args, err)
	}
	names := map[string]bool{}
	keys := map[string]bool{}
	for _, a := range workflow.Actions {
		names[a.Name], keys[a.Key] = true, true
	}
	for _, name := range []string{"update", "new-version", "draft", "build", "publish", "migrate", "check", "changes"} {
		if !names[name] {
			t.Error("missing command", name)
		}
	}
	if len(keys) != len(workflow.Actions) {
		t.Error("dashboard keys aren't unique")
	}
	usage := Usage()
	for _, a := range workflow.Actions {
		if !strings.Contains(usage, a.Name) {
			t.Error("--help lacks", a.Name)
		}
	}
}

func TestPositionalArgumentsAndErrors(t *testing.T) {
	newVersion, _ := workflow.ActionByName("new-version")
	if args, err := newVersion.ParseArgs([]string{"2.0.0"}); err != nil || args.Version != "2.0.0" {
		t.Error(args, err)
	}
	if _, err := newVersion.ParseArgs([]string{"2.0.0", "extra"}); err == nil {
		t.Error("an extra argument should fail")
	}
	check, _ := workflow.ActionByName("check")
	if _, err := check.ParseArgs([]string{"--nope"}); err == nil {
		t.Error("an unknown flag should fail")
	}
	if _, _, _, err := parseGlobal([]string{"--bogus"}); err == nil {
		t.Error("an unknown global option should fail")
	}
}

func TestStatusCommand(t *testing.T) {
	t.Setenv("MODPACK_TOOL_CONFIG", filepath.Join(t.TempDir(), "config.yml"))
	t.Setenv("MODPACK_TOOL_CACHE", t.TempDir())
	pw := testutil.PackDir(t)
	var out bytes.Buffer
	code := Main(Options{Args: []string{"--project", filepath.Dir(pw), "status"}, Stdin: strings.NewReader(""), Stdout: &out})
	if code != ExitOK || !strings.Contains(out.String(), "MyPack 1.2.0 (not released yet)") ||
		!strings.Contains(out.String(), "Next: Draft changelog (3), then edit it.") {
		t.Errorf("exit %d:\n%s", code, out.String())
	}
	out.Reset()
	if code := Main(Options{Args: []string{"--project", t.TempDir(), "status"}, Stdin: strings.NewReader(""), Stdout: &out}); code != ExitUsage ||
		!strings.Contains(out.String(), "No Packwiz") {
		t.Errorf("a folder without a pack: exit %d\n%s", code, out.String())
	}
	out.Reset()
	if code := Main(Options{Args: []string{"frobnicate"}, Stdout: &out}); code != ExitUsage {
		t.Error("an unknown command should be a usage error", code)
	}
}
