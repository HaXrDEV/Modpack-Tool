package tui

import (
	"context"
	"fmt"
	"io"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	tea "charm.land/bubbletea/v2"

	"github.com/HaXrDEV/Modpack-Tool/internal/ui"
)

// The workflow runs in its own goroutine and reaches the screen through one
// buffered channel of events. A listen command drains it in batches, so a
// burst of packwiz output costs a few renders, and events keep their order.

type event interface{}

type stepStarted struct {
	id    int
	title string
}
type stepProgress struct{ id, done, total int }
type stepEnded struct {
	id     int
	detail string
	err    error
}
type logLine struct{ text string }
type note struct {
	warn  bool
	msg   string
	items []string
}
type resultEvent struct{ summary, next string }
type runDone struct{ err error }

// promptEvent asks a question; the answer goes to reply (buffered, so the UI
// never blocks on it).
type promptEvent struct {
	prompt prompt
	reply  chan any
}

// eventsMsg is a batch of events for the run screen.
type eventsMsg []event

// session is the TUI's ui.Session.
type session struct {
	events  chan event
	appDone <-chan struct{}
	log     io.Writer // The full log of the run, or nil.
	logMu   sync.Mutex
	steps   atomic.Int32
}

func newSession(appDone <-chan struct{}, log io.Writer) *session {
	return &session{events: make(chan event, 1024), appDone: appDone, log: log}
}

var _ ui.Session = (*session)(nil)

func (s *session) emit(e event) {
	select {
	case s.events <- e:
	case <-s.appDone: // The app is gone: drop it.
	}
}

func (s *session) writeLog(format string, args ...any) {
	if s.log == nil {
		return
	}
	s.logMu.Lock()
	defer s.logMu.Unlock()
	fmt.Fprintf(s.log, format+"\n", args...)
}

type step struct {
	s  *session
	id int
}

func (s *session) Step(title string) ui.Step {
	id := int(s.steps.Add(1))
	s.writeLog("» %s", title)
	s.emit(stepStarted{id, title})
	return &step{s, id}
}

func (st *step) Progress(done, total int) { st.s.emit(stepProgress{st.id, done, total}) }

func (st *step) Done(detail string) {
	if detail != "" {
		st.s.writeLog("✓ %s", detail)
	}
	st.s.emit(stepEnded{id: st.id, detail: detail})
}

func (st *step) Fail(err error) {
	st.s.writeLog("✗ %v", err)
	st.s.emit(stepEnded{id: st.id, err: err})
}

func (s *session) Log(line string) {
	s.writeLog("  | %s", line)
	s.emit(logLine{line})
}

func (s *session) Info(msg string, items ...string) { s.addNote(note{msg: msg, items: items}, "  ") }

func (s *session) Warn(msg string, items ...string) {
	s.addNote(note{warn: true, msg: msg, items: items}, "! ")
}

func (s *session) addNote(n note, prefix string) {
	s.writeLog("%s%s", prefix, n.msg)
	for _, item := range n.items {
		s.writeLog("    - %s", item)
	}
	s.emit(n)
}

func (s *session) Result(summary, next string) {
	s.writeLog("✓ %s  Next: %s", summary, next)
	s.emit(resultEvent{summary, next})
}

// ask shows a prompt and waits for the answer or a cancel.
func (s *session) ask(ctx context.Context, p prompt) (any, error) {
	reply := make(chan any, 1)
	select {
	case s.events <- promptEvent{p, reply}:
	case <-ctx.Done():
		return nil, ctx.Err()
	case <-s.appDone:
		return nil, context.Canceled
	}
	select {
	case answer := <-reply:
		s.writeLog("? %s: %v", p.question(), answer)
		return answer, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	case <-s.appDone:
		return nil, context.Canceled
	}
}

func (s *session) Confirm(ctx context.Context, question string, def bool) (bool, error) {
	answer, err := s.ask(ctx, newConfirm(question, def))
	if err != nil {
		return false, err
	}
	return answer.(bool), nil
}

func (s *session) Choose(ctx context.Context, question string, options []ui.Option, def string) (string, error) {
	answer, err := s.ask(ctx, newChoose(question, options, def))
	if err != nil {
		return "", err
	}
	return answer.(string), nil
}

func (s *session) PickMany(ctx context.Context, question string, labels []string) ([]int, error) {
	if len(labels) == 0 {
		return nil, nil
	}
	answer, err := s.ask(ctx, newPickMany(question, labels))
	if err != nil {
		return nil, err
	}
	return answer.([]int), nil
}

func (s *session) Ask(ctx context.Context, question, def string) (string, error) {
	answer, err := s.ask(ctx, newText(question, def, false))
	if err != nil {
		return "", err
	}
	if text := strings.TrimSpace(answer.(string)); text != "" {
		return text, nil
	}
	return def, nil
}

func (s *session) AskPath(ctx context.Context, question string) (string, error) {
	answer, err := s.ask(ctx, newText(question, "", true))
	if err != nil {
		return "", err
	}
	return ui.CleanPath(answer.(string)), nil
}

func (s *session) WaitForEdit(ctx context.Context, path string) error {
	message := "Save your edits in the editor, then press Enter."
	if ui.OpenFile(path) != nil {
		message = "Open " + path + " in your editor, save your edits, then press Enter."
	}
	_, err := s.ask(ctx, newWait(message))
	return err
}

// listen waits for the next events: at most one message per 50 ms, except
// that prompts and the end of the run go out at once.
func listen(events <-chan event) tea.Cmd {
	return func() tea.Msg {
		first, ok := <-events
		if !ok {
			return nil
		}
		batch := eventsMsg{first}
		timeout := time.After(50 * time.Millisecond)
		for !urgent(batch[len(batch)-1]) && len(batch) < 1000 {
			select {
			case e := <-events:
				batch = append(batch, e)
			case <-timeout:
				return batch
			}
		}
		return batch
	}
}

func urgent(e event) bool {
	switch e.(type) {
	case promptEvent, runDone:
		return true
	}
	return false
}
