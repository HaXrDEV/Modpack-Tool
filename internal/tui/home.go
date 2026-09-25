package tui

import (
	"fmt"
	"strings"

	"charm.land/bubbles/v2/key"
	"charm.land/bubbles/v2/spinner"
	tea "charm.land/bubbletea/v2"

	"github.com/HaXrDEV/Modpack-Tool/internal/workflow"
)

// statusMsg carries a freshly computed status.
type statusMsg struct {
	status workflow.Status
	seq    int
}

// tagsMsg reports the background tag fetch.
type tagsMsg struct{ ok bool }

// homeScreen is the dashboard: the status card and the actions.
type homeScreen struct {
	app     *App
	status  *workflow.Status
	loading bool
	seq     int // Which status load is current.
	cursor  int
	notice  string
	spinner spinner.Model
}

func newHome(app *App) *homeScreen {
	h := &homeScreen{app: app, spinner: spinner.New(spinner.WithSpinner(app.theme.G.Spinner))}
	h.spinner.Style = app.theme.AccentText
	return h
}

// reload starts loading the status in the background.
func (h *homeScreen) reload() tea.Cmd {
	if h.app.env == nil {
		return nil
	}
	h.loading = true
	h.seq++
	return tea.Batch(h.app.loadStatus(h.seq), h.spinner.Tick)
}

func (h *homeScreen) update(msg tea.Msg) (screen, tea.Cmd) {
	switch msg := msg.(type) {
	case statusMsg:
		if msg.seq == h.seq {
			h.status, h.loading = &msg.status, false
			if action, ok := workflow.ActionByName(msg.status.NextKey); ok {
				h.cursor = actionIndex(action.Key)
			}
		}
	case tagsMsg:
		if !msg.ok {
			h.notice = "Couldn't fetch tags from GitHub; release information may be out of date."
			return h, nil
		}
		return h, h.reload()
	case spinner.TickMsg:
		if !h.loading {
			return h, nil
		}
		var cmd tea.Cmd
		h.spinner, cmd = h.spinner.Update(msg)
		return h, cmd
	case tea.KeyPressMsg:
		return h, h.key(msg.String())
	}
	return h, nil
}

func actionIndex(k string) int {
	for i, a := range workflow.Actions {
		if a.Key == k {
			return i
		}
	}
	return 0
}

func (h *homeScreen) key(k string) tea.Cmd {
	switch k {
	case "up", "k":
		h.cursor = (h.cursor + len(workflow.Actions) - 1) % len(workflow.Actions)
	case "down", "j":
		h.cursor = (h.cursor + 1) % len(workflow.Actions)
	case "1", "2", "3", "4", "5", "6", "7", "8":
		// Digits only move the cursor, so a stray key never starts a workflow.
		h.cursor = actionIndex(k)
	case "enter":
		if h.app.env == nil {
			return func() tea.Msg { return showProjects{} }
		}
		action := workflow.Actions[h.cursor]
		h.notice = ""
		return func() tea.Msg { return startAction{action} }
	case "r":
		return h.reload()
	case "p":
		return func() tea.Msg { return showProjects{} }
	case "?", "h":
		return func() tea.Msg { return showHelp{} }
	case "q", "ctrl+c", "esc":
		return func() tea.Msg { return quitApp{} }
	}
	return nil
}

func (h *homeScreen) view(width, height int) string {
	t := h.app.theme
	var parts []string
	parts = append(parts, h.card(width))
	for i, a := range workflow.Actions {
		marker := "  "
		label := fmt.Sprintf("%s  %-18s", a.Key, a.Label)
		if i == h.cursor {
			marker = t.AccentText.Render(t.G.Cursor + " ")
			label = t.AccentText.Bold(true).Render(label)
		}
		parts = append(parts, truncate(marker+label+" "+t.Faint.Render(a.Summary), width))
	}
	if h.notice != "" {
		parts = append(parts, "", t.WarnText.Render(wrap(h.notice, width)))
	}
	return strings.Join(parts, "\n")
}

func (h *homeScreen) card(width int) string {
	t := h.app.theme
	inner := width - 4
	var lines []string
	switch {
	case h.app.env == nil:
		lines = []string{"No modpack project yet.", t.Faint.Render("Press enter or p to choose the folder that contains Packwiz.")}
	case h.status == nil:
		lines = []string{h.spinner.View() + " Loading the pack status…"}
	default:
		s := h.status
		state := t.WarnText.Render("not released")
		if s.Tag != "" {
			state = t.OKText.Render("released") + t.Faint.Render(" (tag "+s.Tag+")")
		}
		left := t.Bold.Render(s.Version) + t.Faint.Render(" · ") + state
		right := t.Faint.Render(fmt.Sprintf("Minecraft %s · %s %s", s.Minecraft, s.Loader, s.LoaderVersion))
		lines = append(lines, spread(left, right, inner))
		switch {
		case s.ChangesError != "":
			lines = append(lines, t.ErrorText.Render("Changes: ")+s.ChangesError)
		case s.Base != "":
			lines = append(lines, t.Faint.Render("Since "+s.Base+"  ")+s.Changes.Summary())
		case s.IsRepo:
			lines = append(lines, t.Faint.Render("No earlier release tag found."))
		}
		switch {
		case s.ChangelogError != "":
			lines = append(lines, t.ErrorText.Render("Changelog: ")+firstLine(s.ChangelogError))
		case !s.ChangelogExists:
			lines = append(lines, t.Faint.Render("Changelog  ")+s.ChangelogName+t.Faint.Render(" doesn't exist yet"))
		default:
			lines = append(lines, t.Faint.Render("Changelog  ")+fmt.Sprintf("overview %s · config changes %s",
				lineCount(s.OverviewLines), lineCount(s.ConfigLines)))
		}
		next := t.AccentText.Bold(true).Render("Next  ") + s.Next
		if h.loading {
			next += "  " + h.spinner.View()
		}
		lines = append(lines, next)
	}
	for i, line := range lines {
		lines[i] = truncate(line, inner)
	}
	return t.Card.Width(width).Render(strings.Join(lines, "\n"))
}

func lineCount(n int) string {
	switch n {
	case 0:
		return "empty"
	case 1:
		return "1 line"
	}
	return fmt.Sprintf("%d lines", n)
}

func firstLine(text string) string {
	first, _, _ := strings.Cut(text, "\n")
	return first
}

func (h *homeScreen) keys() []key.Binding {
	return []key.Binding{binding("↑↓", "select"), binding("enter", "run"), binding("p", "projects"),
		binding("?", "help"), binding("q", "quit")}
}
