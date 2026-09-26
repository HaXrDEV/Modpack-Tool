package tui

import (
	"strings"

	"charm.land/bubbles/v2/key"
	"charm.land/bubbles/v2/spinner"
	"charm.land/bubbles/v2/viewport"
	tea "charm.land/bubbletea/v2"

	"github.com/HaXrDEV/Modpack-Tool/internal/workflow"
)

// pagedMsg delivers text loaded in the background (the changes report).
type pagedMsg struct {
	lines []string
	err   error
}

// pagerScreen shows scrollable text: the help and the View changes report.
type pagerScreen struct {
	theme   *Theme
	title   string
	vp      viewport.Model
	loading bool
	spinner spinner.Model
}

func newPager(theme *Theme, title string, lines []string) *pagerScreen {
	p := &pagerScreen{theme: theme, title: title, vp: viewport.New(),
		spinner: spinner.New(spinner.WithSpinner(theme.G.Spinner))}
	p.spinner.Style = theme.AccentText
	p.vp.SoftWrap = true
	p.vp.SetContentLines(lines)
	return p
}

func (p *pagerScreen) update(msg tea.Msg) (screen, tea.Cmd) {
	switch msg := msg.(type) {
	case pagedMsg:
		p.loading = false
		lines := msg.lines
		if msg.err != nil {
			lines = []string{p.theme.ErrorText.Render(p.theme.G.Fail+" ") + errorText(msg.err)}
		}
		p.vp.SetContentLines(lines)
		p.vp.GotoTop()
	case spinner.TickMsg:
		if p.loading {
			var cmd tea.Cmd
			p.spinner, cmd = p.spinner.Update(msg)
			return p, cmd
		}
	case tea.KeyPressMsg:
		switch msg.String() {
		case "esc", "q", "enter", "?", "h":
			return p, func() tea.Msg { return goHome{} }
		case "ctrl+c":
			return p, func() tea.Msg { return quitApp{} }
		}
		var cmd tea.Cmd
		p.vp, cmd = p.vp.Update(msg)
		return p, cmd
	}
	return p, nil
}

func (p *pagerScreen) view(width, height int) string {
	title := p.theme.Title.Render(p.title)
	if p.loading {
		return title + "\n" + p.spinner.View() + " Comparing with the last release…"
	}
	p.vp.SetWidth(width)
	p.vp.SetHeight(max(1, height-1))
	return title + "\n" + p.vp.View()
}

func (p *pagerScreen) keys() []key.Binding {
	return []key.Binding{binding("↑↓", "scroll"), binding("esc", "back")}
}

// helpLines is the help screen's text: the keys, then the overview.
func helpLines(overview string) []string {
	keys := []string{
		"KEYS",
		"  ↑↓ or 1-" + workflow.Actions[len(workflow.Actions)-1].Key + "   select an action          enter   run it",
		"  p           switch, add or remove projects",
		"  l           while an action runs: the full log",
		"  esc         cancel the running action (packwiz and git finish their step first)",
		"  ctrl+c      cancel; twice: quit",
		"",
	}
	return append(keys, strings.Split(overview, "\n")...)
}
