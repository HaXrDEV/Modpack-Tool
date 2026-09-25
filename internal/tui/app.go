package tui

import (
	"context"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"charm.land/bubbles/v2/help"
	"charm.land/bubbles/v2/key"
	tea "charm.land/bubbletea/v2"

	"github.com/HaXrDEV/Modpack-Tool/internal/app"
	"github.com/HaXrDEV/Modpack-Tool/internal/config"
	"github.com/HaXrDEV/Modpack-Tool/internal/fail"
	"github.com/HaXrDEV/Modpack-Tool/internal/git"
	"github.com/HaXrDEV/Modpack-Tool/internal/ui"
	"github.com/HaXrDEV/Modpack-Tool/internal/workflow"
)

// screen is one of the app's screens. It renders exactly the body size the
// root gives it, so the header and the footer never move.
type screen interface {
	update(msg tea.Msg) (screen, tea.Cmd)
	view(width, height int) string
	keys() []key.Binding
}

// Navigation requests; only the root handles them.
type (
	goHome       struct{ notice string }
	startAction  struct{ action workflow.Action }
	showProjects struct{}
	showHelp     struct{}
	openProject  struct{ root string }
	quitApp      struct{}
	projectReady struct {
		env   *workflow.Env
		notes []string
	}
)

// App is the root model.
type App struct {
	ctx    context.Context // Canceled when the app exits; the parent of every run.
	cfg    *config.Config
	theme  *Theme
	fancy  bool
	width  int
	height int
	screen screen
	home   *homeScreen
	env    *workflow.Env // The open project; nil when there is none.
	// projectName is the open project's name for the header (the project
	// itself belongs to a running workflow).
	projectName string
	runs        *sync.WaitGroup
	running     bool
	help        help.Model
	// lastResult is printed after the app exits, so something stays on screen.
	lastResult string
	logPath    string
	// startRoot is the project to open first.
	startRoot string
}

func newApp(ctx context.Context, cfg *config.Config, root string, runs *sync.WaitGroup) *App {
	fancy := fancyTerminal()
	a := &App{ctx: ctx, cfg: cfg, theme: NewTheme(true, fancy), fancy: fancy, runs: runs,
		help: help.New(), logPath: config.LogPath(), startRoot: root}
	a.home = newHome(a)
	a.screen = a.home
	return a
}

func (a *App) Init() tea.Cmd {
	cmds := []tea.Cmd{tea.RequestBackgroundColor}
	if a.startRoot != "" {
		cmds = append(cmds, a.open(a.startRoot))
	} else {
		cmds = append(cmds, func() tea.Msg { return showProjects{} })
	}
	return tea.Batch(cmds...)
}

func (a *App) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		a.width, a.height = msg.Width, msg.Height
		return a, nil
	case tea.BackgroundColorMsg:
		*a.theme = *NewTheme(msg.IsDark(), a.fancy)
		return a, nil
	case quitApp:
		return a, tea.Quit
	case goHome:
		a.screen = a.home
		if msg.notice != "" {
			a.home.notice = msg.notice
		}
		return a, a.home.reload()
	case showProjects:
		if a.running {
			return a, nil
		}
		p := newProjects(a)
		a.screen = p
		return a, p.init()
	case showHelp:
		a.screen = newPager(a.theme, "Help", helpLines(workflow.Help(config.DefaultPath(), config.CacheDir())))
		return a, nil
	case openProject:
		return a, a.open(msg.root)
	case projectReady:
		a.env = msg.env
		a.projectName = msg.env.Project.Name
		a.home.status, a.home.notice = nil, strings.Join(msg.notes, " ")
		a.screen = a.home
		return a, tea.Batch(a.home.reload(), a.fetchTags())
	case startAction:
		return a, a.start(msg.action)
	case runFinished:
		a.running = false
		a.screen = a.home
		if msg.err == nil && msg.result != "" {
			a.home.notice = ""
		}
		return a, a.home.reload()
	}
	var cmd tea.Cmd
	a.screen, cmd = a.screen.update(msg)
	return a, cmd
}

// runFinished is sent when the user leaves a finished run.
type runFinished struct {
	err    error
	result string
}

func (a *App) View() tea.View {
	view := tea.NewView(a.render())
	view.AltScreen = true
	view.WindowTitle = "HaXr's Modpack Tool"
	return view
}

func (a *App) render() string {
	if a.width == 0 {
		return ""
	}
	t := a.theme
	width := max(20, min(a.width-4, 100))
	right := a.projectName
	if a.home.status != nil {
		right = a.home.status.Name + " " + a.home.status.Version
	}
	header := spread(t.Bold.Render("HaXr's Modpack Tool"), t.Faint.Render(right), width)
	a.help.SetWidth(width)
	footer := a.help.ShortHelpView(a.screen.keys())
	bodyHeight := max(3, a.height-3)
	body := a.screen.view(width, bodyHeight)
	lines := strings.Split(body, "\n")
	if len(lines) > bodyHeight {
		lines = lines[:bodyHeight]
	}
	for len(lines) < bodyHeight {
		lines = append(lines, "")
	}
	pad := "  "
	out := []string{pad + header}
	for _, line := range lines {
		out = append(out, pad+line)
	}
	return strings.Join(append(out, "", pad+footer), "\n")
}

// open opens a project through a short run, so the one question a first
// import of old settings asks can be answered; it closes by itself when
// everything went fine.
func (a *App) open(root string) tea.Cmd {
	session := newSession(a.ctx.Done(), nil)
	ctx, cancel := context.WithCancel(a.ctx)
	r := newRunScreen(a.theme, "Opening "+filepath.Base(root), session.events, cancel)
	r.autoClose = true
	var env *workflow.Env
	var notes []string
	r.onDone = func(err error) tea.Msg {
		if err != nil {
			return showProjects{}
		}
		return projectReady{env, notes}
	}
	a.screen = r
	a.runs.Add(1)
	go func() {
		defer a.runs.Done()
		var err error
		env, notes, err = app.Open(ctx, a.cfg, root, session)
		if err == nil && env == nil {
			err = fail.Errorf("Couldn't open %s.", root)
		}
		for _, n := range notes {
			session.Info(n)
		}
		session.emit(runDone{err})
	}()
	return r.init()
}

// fetchTags fetches release tags in the background.
func (a *App) fetchTags() tea.Cmd {
	env := a.env
	if env == nil || !env.Git.IsRepo() {
		return nil
	}
	return func() tea.Msg { return tagsMsg{env.Git.FetchTags(a.ctx)} }
}

// loadStatus computes the status on a copy of the project, so it never
// races with a workflow.
func (a *App) loadStatus(seq int) tea.Cmd {
	env := a.statusEnv()
	return func() tea.Msg { return statusMsg{workflow.ComputeStatus(a.ctx, env), seq} }
}

func (a *App) statusEnv() *workflow.Env {
	projectCopy := *a.env.Project
	env := *a.env
	env.Project = &projectCopy
	env.UI = ui.Discard
	env.Git = git.New(projectCopy.Root) // Fresh, so tags made by a run are seen.
	return &env
}

// start runs an action in its own goroutine, reporting to a run screen.
func (a *App) start(action workflow.Action) tea.Cmd {
	if a.env == nil || a.running {
		return nil
	}
	if action.Name == "changes" {
		pager := newPager(a.theme, "Changes since the last release", nil)
		pager.loading = true
		a.screen = pager
		env := a.statusEnv()
		return tea.Batch(pager.spinner.Tick, func() tea.Msg {
			lines, err := workflow.ChangesReport(a.ctx, env, "")
			return pagedMsg{lines, err}
		})
	}
	log := a.openLog(action)
	session := newSession(a.ctx.Done(), log)
	ctx, cancel := context.WithCancel(a.ctx)
	p := a.env.Project // The workflow owns it until the run ends.
	env := workflow.NewEnv(session, p, a.cfg.PackwizExe(), a.env.API, config.CacheDir())
	title := action.Label + ": " + p.Name + " " + p.Version
	r := newRunScreen(a.theme, title, session.events, cancel)
	r.onDone = func(err error) tea.Msg {
		result := ""
		if r.result != nil {
			result = r.result.summary
		}
		a.lastResult = title + ": " + outcome(err, result)
		return runFinished{err, result}
	}
	a.screen, a.running = r, true
	a.runs.Add(1)
	go func() {
		defer a.runs.Done()
		defer func() {
			if log != nil {
				log.Close()
			}
		}()
		err := app.Run(ctx, env, action, workflow.Args{})
		cancel()
		session.emit(runDone{err})
	}()
	return r.init()
}

func outcome(err error, result string) string {
	switch {
	case err != nil:
		return errorText(err)
	case result != "":
		return result
	}
	return "done"
}

// openLog starts the log file of a run (the full-screen view disappears on exit).
func (a *App) openLog(action workflow.Action) io.WriteCloser {
	if err := os.MkdirAll(filepath.Dir(a.logPath), 0o755); err != nil {
		return nil
	}
	f, err := os.Create(a.logPath)
	if err != nil {
		return nil
	}
	io.WriteString(f, action.Label+" ("+a.env.Project.Name+" "+a.env.Project.Version+")\n")
	return f
}
