// Package uitest drives a plain session with scripted answers, the way the
// Python tests replaced input().
package uitest

import (
	"bytes"
	"strings"

	"github.com/HaXrDEV/Modpack-Tool/internal/ui"
)

// Session is a plain session that answers prompts from a script. Running out
// of answers cancels the run, so an unexpected prompt fails the test.
type Session struct {
	*ui.Plain
	Output *bytes.Buffer
	Opened []string
}

// New returns a session with the given answers, one per prompt.
func New(answers ...string) *Session {
	input := ""
	if len(answers) > 0 {
		input = strings.Join(answers, "\n") + "\n"
	}
	out := &bytes.Buffer{}
	s := &Session{Plain: ui.NewPlain(strings.NewReader(input), out), Output: out}
	s.Plain.Open = func(path string) error {
		s.Opened = append(s.Opened, path)
		return nil
	}
	return s
}

// Text is everything the session printed.
func (s *Session) Text() string { return s.Output.String() }
