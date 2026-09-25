package tui

import (
	"context"
	"errors"
	"io"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"
	"github.com/charmbracelet/x/ansi"

	"github.com/HaXrDEV/Modpack-Tool/internal/config"
	"github.com/HaXrDEV/Modpack-Tool/internal/platform"
	"github.com/HaXrDEV/Modpack-Tool/internal/project"
	"github.com/HaXrDEV/Modpack-Tool/internal/testutil"
	"github.com/HaXrDEV/Modpack-Tool/internal/ui"
	"github.com/HaXrDEV/Modpack-Tool/internal/workflow"
)

// runner is a small stand-in for tea.Program: commands run in goroutines and
// their messages are fed to Update on the test goroutine, so the test can
// look at the model between messages without races.
type runner struct {
	t     *testing.T
	app   *App
	msgs  chan tea.Msg
	quit  bool
	runs  *sync.WaitGroup
	stop  context.CancelFunc
	width int
}

func newRunner(t *testing.T, width, height int) *runner {
	t.Helper()
	t.Setenv("MODPACK_TOOL_CACHE", t.TempDir())
	t.Setenv("MODPACK_TOOL_CONFIG", filepath.Join(t.TempDir(), "config.yml"))
	pw := testutil.PackDir(t)
	p, _, err := project.Open(filepath.Dir(pw), nil)
	if err != nil {
		t.Fatal(err)
	}
	cfg, _ := config.Load(config.DefaultPath())
	ctx, cancel := context.WithCancel(context.Background())
	runs := &sync.WaitGroup{}
	a := newApp(ctx, cfg, "", runs)
	*a.theme = *NewTheme(true, false)
	a.env = workflow.NewEnv(ui.Discard, p, "packwiz-not-installed", &platform.Fake{}, config.CacheDir())
	r := &runner{t: t, app: a, msgs: make(chan tea.Msg, 100), runs: runs, stop: cancel, width: width}
	t.Cleanup(func() {
		cancel()
		runs.Wait()
	})
	r.send(tea.WindowSizeMsg{Width: width, Height: height})
	return r
}

// exec runs a command in the background, flattening batches.
func (r *runner) exec(cmd tea.Cmd) {
	if cmd == nil {
		return
	}
	go func() {
		msg := cmd()
		switch m := msg.(type) {
		case nil:
		case tea.BatchMsg:
			for _, c := range m {
				r.exec(c)
			}
		default:
			r.msgs <- msg
		}
	}()
}

// send delivers one message and runs the command it returns.
func (r *runner) send(msg tea.Msg) {
	if _, ok := msg.(tea.QuitMsg); ok {
		r.quit = true
		return
	}
	_, cmd := r.app.Update(msg)
	r.exec(cmd)
}

func (r *runner) key(k string) {
	msg := tea.KeyPressMsg{Text: k}
	switch k {
	case "enter":
		msg = tea.KeyPressMsg{Code: tea.KeyEnter}
	case "esc":
		msg = tea.KeyPressMsg{Code: tea.KeyEscape}
	case "space":
		msg = tea.KeyPressMsg{Code: tea.KeySpace, Text: " "}
	case "down":
		msg = tea.KeyPressMsg{Code: tea.KeyDown}
	case "ctrl+c":
		msg = tea.KeyPressMsg{Code: 'c', Mod: tea.ModCtrl}
	default:
		msg.Code = rune(k[0])
	}
	r.send(msg)
}

func (r *runner) screen() string { return ansi.Strip(r.app.render()) }

// waitFor feeds messages until the screen shows text (or the check passes).
func (r *runner) waitFor(what string, check func(screen string) bool) {
	r.t.Helper()
	deadline := time.After(5 * time.Second)
	for !check(r.screen()) {
		select {
		case msg := <-r.msgs:
			r.send(msg)
		case <-deadline:
			r.t.Fatalf("timed out waiting for %s; screen:\n%s", what, r.screen())
		}
	}
}

func (r *runner) waitText(text string) {
	r.t.Helper()
	r.waitFor(text, func(s string) bool { return strings.Contains(s, text) })
}

// action is a test action with its own workflow.
func action(run func(ctx context.Context, env *workflow.Env) error) workflow.Action {
	return workflow.Action{Key: "9", Name: "test", Label: "Test action", Run: func(ctx context.Context, env *workflow.Env, _ workflow.Args) error {
		return run(ctx, env)
	}}
}

// skipStrayKeyGuard makes the current prompt accept keys at once.
func (r *runner) skipStrayKeyGuard() {
	if run, ok := r.app.screen.(*runScreen); ok && run.prompt != nil {
		run.prompt.shown = time.Time{}
	}
}

func TestDashboardShowsTheStatus(t *testing.T) {
	r := newRunner(t, 100, 30)
	r.send(goHome{})
	r.waitText("Next")
	s := r.screen()
	for _, want := range []string{"HaXr's Modpack Tool", "MyPack 1.2.0", "Minecraft 1.21.11 · Fabric 0.18.4",
		"Draft changelog (3), then edit it.", "1  Update mods", "8  View changes", "enter run"} {
		if !strings.Contains(s, want) {
			t.Errorf("screen lacks %q:\n%s", want, s)
		}
	}
	// The cursor starts on the action Next names, and digits only move it.
	if !strings.Contains(s, "> 3  Draft changelog") {
		t.Errorf("cursor not on Draft changelog:\n%s", s)
	}
	r.key("5")
	if s := r.screen(); !strings.Contains(s, "> 5  Publish") || strings.Contains(s, "Publish:") {
		t.Errorf("digit should only move the cursor:\n%s", s)
	}
}

func TestRunAnswersAConfirm(t *testing.T) {
	r := newRunner(t, 100, 30)
	answered := make(chan bool, 1)
	r.send(startAction{action(func(ctx context.Context, env *workflow.Env) error {
		step := env.UI.Step("Doing the first thing")
		step.Done("First thing done")
		ok, err := env.UI.Confirm(ctx, "Keep going?", false)
		answered <- ok
		if err != nil {
			return err
		}
		env.UI.Result("It worked.", "Build release (4).")
		return nil
	})})
	r.waitText("Keep going?")
	r.skipStrayKeyGuard()
	if s := r.screen(); !strings.Contains(s, "+ First thing done") || !strings.Contains(s, "Yes") {
		t.Errorf("screen:\n%s", s)
	}
	r.key("y")
	if !<-answered {
		t.Error("the answer didn't reach the workflow")
	}
	r.waitText("It worked.")
	r.waitText("Next: Build release (4).")
	r.key("enter")
	r.waitFor("the dashboard", func(s string) bool { return strings.Contains(s, "1  Update mods") })
}

func TestCancelDuringAPromptReturnsHome(t *testing.T) {
	r := newRunner(t, 100, 30)
	result := make(chan error, 1)
	r.send(startAction{action(func(ctx context.Context, env *workflow.Env) error {
		_, err := env.UI.Ask(ctx, "New version", "1.2.1")
		result <- err
		return err
	})})
	r.waitText("New version")
	r.skipStrayKeyGuard()
	r.key("esc")
	if err := <-result; !errors.Is(err, context.Canceled) {
		t.Errorf("the prompt returned %v", err)
	}
	r.waitText("Cancelled.")
	r.waitFor("the dashboard", func(s string) bool { return strings.Contains(s, "1  Update mods") })
}

func TestQuittingDuringAPromptStopsTheRun(t *testing.T) {
	r := newRunner(t, 100, 30)
	r.send(startAction{action(func(ctx context.Context, env *workflow.Env) error {
		_, err := env.UI.Confirm(ctx, "Waiting forever?", true)
		return err
	})})
	r.waitText("Waiting forever?")
	r.key("ctrl+c") // Cancels the run...
	r.key("ctrl+c") // ...and a second one quits.
	r.waitFor("quit", func(string) bool { return r.quit })
	r.stop()
	done := make(chan struct{})
	go func() { r.runs.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("the run didn't stop within a second")
	}
}

func TestStrayKeysAreIgnoredRightAfterAPrompt(t *testing.T) {
	r := newRunner(t, 100, 30)
	answered := make(chan bool, 1)
	r.send(startAction{action(func(ctx context.Context, env *workflow.Env) error {
		ok, err := env.UI.Confirm(ctx, "Push to GitHub?", true)
		answered <- ok
		return err
	})})
	r.waitText("Push to GitHub?")
	run := r.app.screen.(*runScreen)
	shown := run.prompt.shown
	run.now = func() time.Time { return shown.Add(50 * time.Millisecond) }
	r.key("enter") // Too soon: a leftover Enter from the dashboard.
	if run.prompt == nil {
		t.Fatal("the stray Enter answered the prompt")
	}
	run.now = func() time.Time { return shown.Add(time.Second) }
	r.key("n")
	if <-answered {
		t.Error("expected no")
	}
}

func TestPickManyAndPaths(t *testing.T) {
	r := newRunner(t, 100, 30)
	picked := make(chan []int, 1)
	folder := make(chan string, 1)
	r.send(startAction{action(func(ctx context.Context, env *workflow.Env) error {
		indexes, err := env.UI.PickMany(ctx, "Keep which alpha versions?", []string{"Sodium", "Iris", "Lithium"})
		if err != nil {
			return err
		}
		picked <- indexes
		path, err := env.UI.AskPath(ctx, "Folder containing these files")
		folder <- path
		return err
	})})
	r.waitText("0 of 3 selected")
	r.skipStrayKeyGuard()
	r.key("down")
	r.key("space")
	r.waitText("1 of 3 selected")
	r.key("enter")
	if got := <-picked; len(got) != 1 || got[0] != 1 {
		t.Errorf("picked %v", got)
	}
	r.waitText("Folder containing these files")
	r.skipStrayKeyGuard()
	r.send(tea.PasteMsg{Content: `"C:\Games\My Instance\mods"`}) // A dropped folder.
	r.key("enter")
	if got := <-folder; got != filepath.Clean(`C:\Games\My Instance\mods`) {
		t.Errorf("path %q", got)
	}
}

// The real Bubble Tea program: input parsing, rendering and quitting.
func TestRealProgramRendersAndQuits(t *testing.T) {
	r := newRunner(t, 80, 24)
	input, typing := io.Pipe()
	output := &lockedBuffer{}
	program := tea.NewProgram(r.app, tea.WithInput(input), tea.WithOutput(output),
		tea.WithWindowSize(80, 24), tea.WithoutSignalHandler())
	done := make(chan error, 1)
	go func() {
		_, err := program.Run()
		done <- err
	}()
	go func() {
		// Without a start folder the app opens on Projects; esc goes to the dashboard.
		for _, keys := range []string{"\x1b", "?", "\x1b", "q"} {
			time.Sleep(300 * time.Millisecond)
			io.WriteString(typing, keys)
		}
	}()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(10 * time.Second):
		program.Kill()
		t.Fatal("the program didn't quit")
	}
	screen := ansi.Strip(output.String())
	for _, want := range []string{"HaXr's Modpack Tool", "Update mods", "HOW A RELEASE WORKS"} {
		if !strings.Contains(screen, want) {
			t.Errorf("output lacks %q", want)
		}
	}
}

type lockedBuffer struct {
	mu  sync.Mutex
	buf strings.Builder
}

func (b *lockedBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *lockedBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

func TestLayoutFitsTheWindow(t *testing.T) {
	for _, size := range [][2]int{{80, 24}, {120, 40}} {
		r := newRunner(t, size[0], size[1])
		r.send(goHome{})
		r.waitText("Next")
		check := func(name string) {
			lines := strings.Split(r.screen(), "\n")
			if len(lines) != size[1] {
				t.Errorf("%s at %dx%d: %d lines", name, size[0], size[1], len(lines))
			}
			for _, line := range lines {
				if w := ansi.StringWidth(line); w > size[0] {
					t.Errorf("%s at %dx%d: line %d wide: %q", name, size[0], size[1], w, line)
				}
			}
		}
		check("dashboard")
		r.send(startAction{action(func(ctx context.Context, env *workflow.Env) error {
			for i := 0; i < 50; i++ {
				env.UI.Info(strings.Repeat("a long line of output ", 8))
			}
			_, err := env.UI.PickMany(ctx, "Pick", strings.Split(strings.Repeat("item ", 40), " ")[:40])
			return err
		})})
		r.waitText("0 of 40 selected")
		check("run screen")
		r.send(showHelp{})
		check("help")
	}
}
