package tui

import (
	"path/filepath"
	"strings"

	"charm.land/bubbles/v2/key"
	"charm.land/bubbles/v2/textinput"
	tea "charm.land/bubbletea/v2"

	"github.com/HaXrDEV/Modpack-Tool/internal/config"
	"github.com/HaXrDEV/Modpack-Tool/internal/ui"
)

// projectsScreen switches, adds and removes known projects.
type projectsScreen struct {
	app      *App
	cursor   int
	adding   bool
	removing bool
	input    textinput.Model
	message  string
}

func newProjects(app *App) *projectsScreen {
	input := textinput.New()
	input.Prompt = ""
	input.Placeholder = "the folder that contains Packwiz (type or drop it here)"
	p := &projectsScreen{app: app, input: input}
	for i, root := range app.cfg.Projects {
		if app.env != nil && config.SamePath(root, app.env.Project.Root) {
			p.cursor = i
		}
	}
	if len(app.cfg.Projects) == 0 {
		p.adding = true
	}
	return p
}

func (p *projectsScreen) init() tea.Cmd {
	if p.adding {
		return p.input.Focus()
	}
	return nil
}

func (p *projectsScreen) update(msg tea.Msg) (screen, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.PasteMsg:
		if p.adding {
			var cmd tea.Cmd
			p.input, cmd = p.input.Update(msg)
			return p, cmd
		}
	case tea.KeyPressMsg:
		return p, p.key(msg)
	}
	return p, nil
}

func (p *projectsScreen) key(msg tea.KeyPressMsg) tea.Cmd {
	projects := p.app.cfg.Projects
	k := msg.String()
	if k == "ctrl+c" {
		return func() tea.Msg { return quitApp{} }
	}
	if p.adding {
		switch k {
		case "esc":
			p.adding = false
			if len(projects) == 0 {
				return func() tea.Msg { return goHome{} }
			}
		case "enter":
			root := ui.CleanPath(p.input.Value())
			if root == "" {
				p.adding = false
				return nil
			}
			p.input.SetValue("")
			p.adding = false
			return func() tea.Msg { return openProject{root} }
		default:
			var cmd tea.Cmd
			p.input, cmd = p.input.Update(msg)
			return cmd
		}
		return nil
	}
	if p.removing {
		p.removing = false
		if k == "y" && p.cursor < len(projects) {
			if err := p.app.cfg.Forget(projects[p.cursor]); err != nil {
				p.message = err.Error()
			}
			p.cursor = max(0, min(p.cursor, len(p.app.cfg.Projects)-1))
		}
		return nil
	}
	switch k {
	case "up", "k":
		p.cursor = max(0, p.cursor-1)
	case "down", "j":
		p.cursor = min(max(0, len(projects)-1), p.cursor+1)
	case "enter":
		if p.cursor < len(projects) {
			root := projects[p.cursor]
			return func() tea.Msg { return openProject{root} }
		}
	case "a":
		p.adding = true
		p.message = ""
		return p.input.Focus()
	case "r", "delete":
		if len(projects) > 0 {
			p.removing = true
		}
	case "esc", "q", "p":
		return func() tea.Msg { return goHome{} }
	}
	return nil
}

func (p *projectsScreen) view(width, height int) string {
	t := p.app.theme
	lines := []string{t.Title.Render("Projects"), ""}
	for i, root := range p.app.cfg.Projects {
		label := filepath.Base(root) + t.Faint.Render("  "+root)
		if p.app.env != nil && config.SamePath(root, p.app.env.Project.Root) {
			label += t.OKText.Render("  (open)")
		}
		if i == p.cursor {
			lines = append(lines, t.AccentText.Render(t.G.Cursor+" ")+label)
		} else {
			lines = append(lines, "  "+label)
		}
	}
	if len(p.app.cfg.Projects) == 0 {
		lines = append(lines, t.Faint.Render("  No projects yet."))
	}
	if p.message != "" {
		lines = append(lines, "", t.ErrorText.Render(wrap(p.message, width)))
	}
	switch {
	case p.adding:
		p.input.SetWidth(max(10, width-8))
		body := "Modpack folder (the one containing 'Packwiz'; drag & drop works)\n\n" +
			t.AccentText.Render(t.G.Cursor+" ") + p.input.View()
		lines = append(lines, "", box(t, t.Accent, "Add a project", "", body, width))
	case p.removing && p.cursor < len(p.app.cfg.Projects):
		body := "Remove " + filepath.Base(p.app.cfg.Projects[p.cursor]) + " from the list? Its files stay untouched. (y/n)"
		lines = append(lines, "", box(t, t.Warn, "Remove project", "", wrap(body, width-4), width))
	}
	return strings.Join(lines, "\n")
}

func (p *projectsScreen) keys() []key.Binding {
	switch {
	case p.adding:
		return []key.Binding{binding("enter", "open"), binding("esc", "back")}
	case p.removing:
		return []key.Binding{binding("y", "remove"), binding("n", "keep")}
	}
	return []key.Binding{binding("enter", "open"), binding("a", "add"), binding("r", "remove"), binding("esc", "back")}
}
