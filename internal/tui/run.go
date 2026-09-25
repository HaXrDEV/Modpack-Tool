package tui

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"charm.land/bubbles/v2/key"
	"charm.land/bubbles/v2/progress"
	"charm.land/bubbles/v2/spinner"
	"charm.land/bubbles/v2/viewport"
	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"

	"github.com/HaXrDEV/Modpack-Tool/internal/fail"
	"github.com/HaXrDEV/Modpack-Tool/internal/ui"
)

const (
	logLimit       = 2000                   // Log lines kept in memory (the log file has all of them).
	logTailLines   = 4                      // Output lines shown under the running step.
	strayKeyWindow = 200 * time.Millisecond // Keys this soon after a prompt appears are ignored.
)

// entry is one finished line of the run's history.
type entry struct {
	kind  string // "ok", "fail", "info", "warn"
	text  string
	items []string
}

type runningStep struct {
	id          int
	title       string
	started     time.Time
	done, total int
	tail        []string
}

type activePrompt struct {
	prompt
	reply chan any
	shown time.Time
}

// runScreen shows a running action: finished steps, the running step with a
// spinner, the prompt panel, and the result. One column, anchored to the bottom.
type runScreen struct {
	theme      *Theme
	title      string
	events     <-chan event
	cancel     context.CancelFunc
	history    []entry
	steps      []*runningStep
	logs       []string
	prompt     *activePrompt
	result     *resultEvent
	err        error
	done       bool
	cancelling bool
	showLog    bool
	autoClose  bool // Go back by itself after a clean finish (opening a project).
	warned     bool
	log        viewport.Model
	spinner    spinner.Model
	bar        progress.Model
	now        func() time.Time
	// onDone is sent to the root when the run ends and the user leaves.
	onDone func(err error) tea.Msg
}

func newRunScreen(theme *Theme, title string, events <-chan event, cancel context.CancelFunc) *runScreen {
	r := &runScreen{theme: theme, title: title, events: events, cancel: cancel, now: time.Now,
		log: viewport.New(), spinner: spinner.New(spinner.WithSpinner(theme.G.Spinner)),
		bar: progress.New(progress.WithoutPercentage(), progress.WithColors(theme.Accent))}
	r.spinner.Style = theme.AccentText
	r.log.SoftWrap = true
	return r
}

func (r *runScreen) init() tea.Cmd { return tea.Batch(listen(r.events), r.spinner.Tick) }

func (r *runScreen) update(msg tea.Msg) (screen, tea.Cmd) {
	switch msg := msg.(type) {
	case eventsMsg:
		var cmds []tea.Cmd
		for _, e := range msg {
			cmds = append(cmds, r.apply(e))
		}
		if !r.done {
			cmds = append(cmds, listen(r.events))
		} else if r.autoClose && r.err == nil && !r.warned {
			cmds = append(cmds, r.leave())
		} else if r.cancelling && errors.Is(r.err, context.Canceled) {
			cmds = append(cmds, func() tea.Msg { return goHome{notice: "Cancelled."} })
		}
		return r, tea.Batch(cmds...)
	case spinner.TickMsg:
		var cmd tea.Cmd
		r.spinner, cmd = r.spinner.Update(msg)
		if r.done {
			return r, nil
		}
		return r, cmd
	case tea.PasteMsg:
		if r.prompt != nil {
			return r, r.answer(msg)
		}
	case tea.KeyPressMsg:
		return r, r.key(msg)
	}
	return r, nil
}

func (r *runScreen) key(msg tea.KeyPressMsg) tea.Cmd {
	k := msg.String()
	if k == "ctrl+c" {
		if r.done {
			return r.leave()
		}
		if r.cancelling { // A second Ctrl+C quits the app.
			return func() tea.Msg { return quitApp{} }
		}
		r.cancelRun()
		return nil
	}
	if r.showLog {
		switch k {
		case "l", "esc", "q":
			r.showLog = false
		default:
			var cmd tea.Cmd
			r.log, cmd = r.log.Update(msg)
			return cmd
		}
		return nil
	}
	if r.prompt != nil {
		if r.now().Sub(r.prompt.shown) < strayKeyWindow {
			return nil
		}
		if k == "esc" {
			r.cancelRun()
			return nil
		}
		return r.answer(msg)
	}
	switch k {
	case "l":
		r.showLog = true
		r.log.SetContentLines(r.logs)
		r.log.GotoBottom()
	case "esc", "enter", "q":
		if r.done {
			return r.leave()
		}
		if k == "esc" {
			r.cancelRun()
		}
	}
	return nil
}

func (r *runScreen) cancelRun() {
	if !r.done && !r.cancelling {
		r.cancelling = true
		r.prompt = nil // The workflow sees the cancel instead of an answer.
		r.cancel()
	}
}

func (r *runScreen) leave() tea.Cmd {
	err := r.err
	return func() tea.Msg { return r.onDone(err) }
}

func (r *runScreen) answer(msg tea.Msg) tea.Cmd {
	done, answer, cmd := r.prompt.update(msg)
	if done {
		r.prompt.reply <- answer
		r.history = append(r.history, entry{kind: "info", text: r.theme.Faint.Render(truncate(r.prompt.question(), 200)) +
			" " + answerText(answer)})
		r.prompt = nil
	}
	return cmd
}

func answerText(answer any) string {
	switch a := answer.(type) {
	case bool:
		if a {
			return "yes"
		}
		return "no"
	case []int:
		return fmt.Sprintf("%d picked", len(a))
	case string:
		if a == "done" || a == "" {
			return ""
		}
		return a
	}
	return ""
}

func (r *runScreen) step(id int) *runningStep {
	for _, s := range r.steps {
		if s.id == id {
			return s
		}
	}
	return nil
}

func (r *runScreen) apply(e event) tea.Cmd {
	switch e := e.(type) {
	case stepStarted:
		r.steps = append(r.steps, &runningStep{id: e.id, title: e.title, started: r.now()})
	case stepProgress:
		if s := r.step(e.id); s != nil {
			s.done, s.total = e.done, e.total
		}
	case stepEnded:
		s := r.step(e.id)
		if s == nil {
			break
		}
		for i, other := range r.steps {
			if other == s {
				r.steps = append(r.steps[:i], r.steps[i+1:]...)
				break
			}
		}
		switch {
		case e.err != nil: // The reason is shown once, when the run ends.
			r.history = append(r.history, entry{kind: "fail", text: s.title})
		case e.detail != "":
			r.history = append(r.history, entry{kind: "ok", text: e.detail})
		default:
			r.history = append(r.history, entry{kind: "ok", text: s.title})
		}
	case logLine:
		r.logs = append(r.logs, e.text)
		if len(r.logs) > logLimit {
			r.logs = r.logs[len(r.logs)-logLimit:]
		}
		if len(r.steps) > 0 {
			s := r.steps[len(r.steps)-1]
			s.tail = append(s.tail, e.text)
			if len(s.tail) > logTailLines {
				s.tail = s.tail[len(s.tail)-logTailLines:]
			}
		}
		if r.showLog {
			atBottom := r.log.AtBottom()
			r.log.SetContentLines(r.logs)
			if atBottom {
				r.log.GotoBottom()
			}
		}
	case note:
		kind := "info"
		if e.warn {
			kind = "warn"
			r.warned = true
		}
		r.history = append(r.history, entry{kind: kind, text: e.msg, items: e.items})
	case resultEvent:
		r.result = &e
	case promptEvent:
		if r.cancelling {
			break // The workflow gets the cancel through its context.
		}
		r.prompt = &activePrompt{prompt: e.prompt, reply: e.reply, shown: r.now()}
		return e.prompt.init()
	case runDone:
		r.done, r.err, r.steps, r.prompt = true, e.err, nil, nil
	}
	return nil
}

func errorText(err error) string {
	var bug *ui.BugError
	switch {
	case errors.Is(err, context.Canceled):
		return "cancelled"
	case errors.As(err, &bug):
		return fmt.Sprintf("unexpected error (a bug in the tool): %v", bug.Value)
	}
	return err.Error()
}

func (r *runScreen) view(width, height int) string {
	t := r.theme
	if r.showLog {
		r.log.SetWidth(width)
		r.log.SetHeight(max(1, height-1))
		return t.Title.Render(r.title+" · log") + "\n" + r.log.View()
	}
	var lines []string
	add := func(text string) { lines = append(lines, strings.Split(text, "\n")...) }
	for _, e := range r.history {
		switch e.kind {
		case "ok":
			add(t.OKText.Render(t.G.OK) + " " + wrapRest(e.text, width-2))
		case "fail":
			add(t.ErrorText.Render(t.G.Fail) + " " + wrapRest(e.text, width-2))
		case "warn":
			add(t.WarnText.Render(t.G.Warn) + " " + wrapRest(e.text, width-2))
		default:
			add("  " + wrapRest(e.text, width-2))
		}
		for _, item := range e.items {
			add(t.Faint.Render("    "+t.G.Bullet+" ") + wrapRest(item, width-6))
		}
	}
	for _, s := range r.steps {
		elapsed := r.now().Sub(s.started).Round(time.Second)
		right := t.Faint.Render(formatElapsed(elapsed))
		if s.total > 0 {
			right = t.Faint.Render(fmt.Sprintf("%d/%d · %s", s.done, s.total, formatElapsed(elapsed)))
		}
		add(spread(r.spinner.View()+" "+s.title, right, width))
		if s.total > 0 {
			r.bar.SetWidth(max(10, min(width-4, 60)))
			add("  " + r.bar.ViewAs(float64(s.done)/float64(s.total)))
		} else {
			for _, line := range s.tail {
				add(t.Faint.Render("  │ " + truncate(line, width-4)))
			}
		}
	}
	if r.done {
		switch {
		case r.err != nil && errors.Is(r.err, context.Canceled):
			add(t.WarnText.Render(t.G.Warn + " Cancelled."))
		case r.err != nil:
			message := errorText(r.err)
			if !fail.Is(r.err) {
				message += " (details in the log: press l)"
			}
			add(t.ErrorText.Render(t.G.Fail) + " " + wrapRest(message, width-2))
		case r.result != nil:
			if r.result.summary != "" {
				add(t.OKText.Render(t.G.OK) + " " + t.Bold.Render(wrapRest(r.result.summary, width-2)))
			}
			if r.result.next != "" {
				add(t.AccentText.Render("  Next: ") + wrapRest(r.result.next, width-8))
			}
		default:
			add(t.OKText.Render(t.G.OK) + " Done.")
		}
	} else if r.cancelling {
		add(r.spinner.View() + " " + t.WarnText.Render("Cancelling… (waiting for the running command to finish)"))
	}

	panel := ""
	if r.prompt != nil {
		innerHeight := max(3, height*2/3-2)
		body := r.prompt.view(t, width-4, innerHeight)
		panel = box(t, t.Accent, r.title, r.prompt.counter(), body, width)
	}
	available := height - 1
	if panel != "" {
		available -= lipgloss.Height(panel)
	}
	lines = bottom(lines, max(0, available))
	// Anchored to the bottom: the newest lines and the prompt sit above the keys.
	gap := make([]string, max(0, available-len(lines)))
	out := t.Title.Render(r.title) + "\n" + strings.Join(append(gap, lines...), "\n")
	if panel != "" {
		out += "\n" + panel
	}
	return out
}

// wrapRest wraps text and indents continuation lines by two spaces.
func wrapRest(text string, width int) string {
	return strings.ReplaceAll(wrap(text, max(10, width)), "\n", "\n  ")
}

func formatElapsed(d time.Duration) string {
	return fmt.Sprintf("%d:%02d", int(d.Minutes()), int(d.Seconds())%60)
}

func (r *runScreen) keys() []key.Binding {
	switch {
	case r.showLog:
		return []key.Binding{binding("↑↓", "scroll"), binding("l", "back")}
	case r.prompt != nil:
		return append(r.prompt.keys(), binding("esc", "cancel"))
	case r.done:
		return []key.Binding{binding("enter", "back to the dashboard"), binding("l", "log")}
	case r.cancelling:
		return []key.Binding{binding("ctrl+c", "quit now"), binding("l", "log")}
	}
	return []key.Binding{binding("l", "log"), binding("esc", "cancel")}
}
