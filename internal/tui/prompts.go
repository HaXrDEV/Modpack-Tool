package tui

import (
	"fmt"
	"strings"

	"charm.land/bubbles/v2/key"
	"charm.land/bubbles/v2/textinput"
	tea "charm.land/bubbletea/v2"

	"github.com/HaXrDEV/Modpack-Tool/internal/ui"
)

// A prompt is one question in the run screen's prompt panel. The keys match
// the plain session's answers: Enter takes the default, y/n, a/n for all/none.
type prompt interface {
	question() string
	// update handles a key or paste; done reports an answer.
	update(msg tea.Msg) (done bool, answer any, cmd tea.Cmd)
	// view renders the panel body for the given width and height.
	view(t *Theme, width, height int) string
	// counter is shown in the panel's top-right corner, e.g. "2 of 14".
	counter() string
	keys() []key.Binding
	init() tea.Cmd
}

func binding(keys, desc string) key.Binding {
	return key.NewBinding(key.WithKeys(strings.Fields(keys)...), key.WithHelp(strings.Fields(keys)[0], desc))
}

func keyText(msg tea.Msg) string {
	if k, ok := msg.(tea.KeyPressMsg); ok {
		return k.String()
	}
	return ""
}

////////////////////////////////////////////////////////////
// Confirm

type confirmPrompt struct {
	text  string
	value bool
}

func newConfirm(question string, def bool) *confirmPrompt { return &confirmPrompt{question, def} }

func (p *confirmPrompt) question() string { return p.text }
func (p *confirmPrompt) counter() string  { return "" }
func (p *confirmPrompt) init() tea.Cmd    { return nil }

func (p *confirmPrompt) update(msg tea.Msg) (bool, any, tea.Cmd) {
	switch keyText(msg) {
	case "y", "Y":
		return true, true, nil
	case "n", "N":
		return true, false, nil
	case "enter":
		return true, p.value, nil
	case "left", "right", "tab", "shift+tab", "h", "l":
		p.value = !p.value
	}
	return false, nil, nil
}

func (p *confirmPrompt) view(t *Theme, width, _ int) string {
	option := func(label string, selected bool) string {
		if selected {
			return t.AccentText.Bold(true).Render(t.G.Cursor + " " + label)
		}
		return t.Faint.Render("  " + label)
	}
	return wrap(p.text, width) + "\n\n" + option("Yes", p.value) + "   " + option("No", !p.value)
}

func (p *confirmPrompt) keys() []key.Binding {
	return []key.Binding{binding("y", "yes"), binding("n", "no"), binding("enter", "default"), binding("←→", "switch")}
}

////////////////////////////////////////////////////////////
// Choose

type choosePrompt struct {
	text    string
	options []ui.Option
	cursor  int
}

func newChoose(question string, options []ui.Option, def string) *choosePrompt {
	p := &choosePrompt{text: question, options: options}
	for i, o := range options {
		if o.Key == def {
			p.cursor = i
		}
	}
	return p
}

func (p *choosePrompt) question() string { return p.text }
func (p *choosePrompt) counter() string  { return "" }
func (p *choosePrompt) init() tea.Cmd    { return nil }

func (p *choosePrompt) update(msg tea.Msg) (bool, any, tea.Cmd) {
	switch k := keyText(msg); k {
	case "up", "k", "shift+tab":
		p.cursor = (p.cursor + len(p.options) - 1) % len(p.options)
	case "down", "j", "tab":
		p.cursor = (p.cursor + 1) % len(p.options)
	case "enter":
		return true, p.options[p.cursor].Key, nil
	default:
		for _, o := range p.options {
			if strings.EqualFold(o.Key, k) {
				return true, o.Key, nil
			}
		}
	}
	return false, nil, nil
}

func (p *choosePrompt) view(t *Theme, width, _ int) string {
	lines := []string{wrap(p.text, width), ""}
	for i, o := range p.options {
		line := fmt.Sprintf("%s  %s", o.Key, o.Label)
		if i == p.cursor {
			lines = append(lines, t.AccentText.Bold(true).Render(t.G.Cursor+" "+line))
		} else {
			lines = append(lines, "  "+line)
		}
	}
	return strings.Join(lines, "\n")
}

func (p *choosePrompt) keys() []key.Binding {
	return []key.Binding{binding("↑↓", "select"), binding("enter", "choose")}
}

////////////////////////////////////////////////////////////
// Pick many

type pickPrompt struct {
	text    string
	labels  []string
	checked []bool
	cursor  int
	offset  int
}

func newPickMany(question string, labels []string) *pickPrompt {
	return &pickPrompt{text: question, labels: labels, checked: make([]bool, len(labels))}
}

func (p *pickPrompt) question() string { return p.text }
func (p *pickPrompt) init() tea.Cmd    { return nil }

func (p *pickPrompt) counter() string {
	n := 0
	for _, c := range p.checked {
		if c {
			n++
		}
	}
	return fmt.Sprintf("%d of %d selected", n, len(p.labels))
}

func (p *pickPrompt) update(msg tea.Msg) (bool, any, tea.Cmd) {
	switch keyText(msg) {
	case "up", "k":
		p.cursor = max(0, p.cursor-1)
	case "down", "j":
		p.cursor = min(len(p.labels)-1, p.cursor+1)
	case "pgup":
		p.cursor = max(0, p.cursor-10)
	case "pgdown":
		p.cursor = min(len(p.labels)-1, p.cursor+10)
	case "space", "x":
		p.checked[p.cursor] = !p.checked[p.cursor]
	case "a":
		for i := range p.checked {
			p.checked[i] = true
		}
	case "n":
		for i := range p.checked {
			p.checked[i] = false
		}
	case "enter":
		var picked []int
		for i, c := range p.checked {
			if c {
				picked = append(picked, i)
			}
		}
		return true, picked, nil
	}
	return false, nil, nil
}

func (p *pickPrompt) view(t *Theme, width, height int) string {
	header := wrap(p.text, width)
	visible := max(3, height-lipHeight(header)-1)
	if p.cursor < p.offset {
		p.offset = p.cursor
	}
	if p.cursor >= p.offset+visible {
		p.offset = p.cursor - visible + 1
	}
	lines := []string{header, ""}
	for i := p.offset; i < len(p.labels) && i < p.offset+visible; i++ {
		box := t.G.Unchecked
		if p.checked[i] {
			box = t.G.Checked
		}
		line := truncate(box+" "+p.labels[i], width-2)
		if i == p.cursor {
			lines = append(lines, t.AccentText.Bold(true).Render(t.G.Cursor+" "+line))
		} else {
			lines = append(lines, "  "+line)
		}
	}
	if more := len(p.labels) - (p.offset + visible); more > 0 {
		lines = append(lines, t.Faint.Render(fmt.Sprintf("  … %d more", more)))
	}
	return strings.Join(lines, "\n")
}

func (p *pickPrompt) keys() []key.Binding {
	return []key.Binding{binding("space", "toggle"), binding("a", "all"), binding("n", "none"), binding("enter", "confirm")}
}

////////////////////////////////////////////////////////////
// Text and path input

type textPrompt struct {
	text  string
	input textinput.Model
	path  bool
}

func newText(question, def string, path bool) *textPrompt {
	input := textinput.New()
	input.Prompt = ""
	input.SetValue(def)
	input.CursorEnd()
	if path {
		input.Placeholder = "type or drop a folder here"
	}
	return &textPrompt{text: question, input: input, path: path}
}

func (p *textPrompt) question() string { return p.text }
func (p *textPrompt) counter() string  { return "" }
func (p *textPrompt) init() tea.Cmd    { return p.input.Focus() }

func (p *textPrompt) update(msg tea.Msg) (bool, any, tea.Cmd) {
	if keyText(msg) == "enter" {
		return true, p.input.Value(), nil
	}
	var cmd tea.Cmd
	p.input, cmd = p.input.Update(msg)
	return false, nil, cmd
}

func (p *textPrompt) view(t *Theme, width, _ int) string {
	p.input.SetWidth(max(10, width-4))
	return wrap(p.text, width) + "\n\n" + t.AccentText.Render(t.G.Cursor+" ") + p.input.View()
}

func (p *textPrompt) keys() []key.Binding {
	if p.path {
		return []key.Binding{binding("enter", "use this folder (empty: cancel)")}
	}
	return []key.Binding{binding("enter", "confirm")}
}

////////////////////////////////////////////////////////////
// Wait for an edit

type waitPrompt struct{ text string }

func newWait(message string) *waitPrompt { return &waitPrompt{message} }

func (p *waitPrompt) question() string { return p.text }
func (p *waitPrompt) counter() string  { return "" }
func (p *waitPrompt) init() tea.Cmd    { return nil }

func (p *waitPrompt) update(msg tea.Msg) (bool, any, tea.Cmd) {
	return keyText(msg) == "enter", "done", nil
}

func (p *waitPrompt) view(_ *Theme, width, _ int) string { return wrap(p.text, width) }

func (p *waitPrompt) keys() []key.Binding { return []key.Binding{binding("enter", "done")} }
