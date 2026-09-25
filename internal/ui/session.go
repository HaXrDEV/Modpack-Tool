// Package ui is the boundary between the workflows and the screen. Workflows
// talk only to a Session: the full-screen app (internal/tui) implements it,
// and so does the plain line-by-line session used for subcommands, scripts
// and tests. Nothing here depends on the terminal UI libraries.
package ui

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

// Session is how a workflow shows progress and asks questions. Prompts
// return context.Canceled when the run is canceled (Esc, Ctrl+C, end of input).
// A Session is safe for concurrent use.
type Session interface {
	// Step starts a step: a spinner row that ends with ✓ or ✗.
	Step(title string) Step
	// Log shows a line of tool output (packwiz, git, gh), muted.
	Log(line string)
	// Info and Warn show a message, optionally with a list of items.
	Info(msg string, items ...string)
	Warn(msg string, items ...string)
	// Result is the outcome of the action and the next thing to do.
	Result(summary, next string)

	Confirm(ctx context.Context, question string, def bool) (bool, error)
	// Choose asks for one of the options and returns its Key.
	Choose(ctx context.Context, question string, options []Option, def string) (string, error)
	// PickMany lets the user pick some of the labels (none by default) and
	// returns their indexes in order.
	PickMany(ctx context.Context, question string, labels []string) ([]int, error)
	Ask(ctx context.Context, question, def string) (string, error)
	// AskPath asks for a folder or file; typed and dropped paths are cleaned.
	AskPath(ctx context.Context, question string) (string, error)
	// WaitForEdit opens a file in its default app and waits until the user
	// is done editing it.
	WaitForEdit(ctx context.Context, path string) error
}

// Step is a running step.
type Step interface {
	Progress(done, total int)
	Done(detail string)
	Fail(err error)
}

// Option is one answer of Choose.
type Option struct{ Key, Label string }

// Pick is PickMany for typed items: it returns the picked items.
func Pick[T any](ctx context.Context, s Session, question string, items []T, label func(T) string) ([]T, error) {
	if len(items) == 0 {
		return nil, nil
	}
	labels := make([]string, len(items))
	for i, item := range items {
		labels[i] = label(item)
	}
	indexes, err := s.PickMany(ctx, question, labels)
	if err != nil {
		return nil, err
	}
	picked := make([]T, 0, len(indexes))
	for _, i := range indexes {
		picked = append(picked, items[i])
	}
	return picked, nil
}

// Limit shortens a list to n items plus an "… and N more" line.
func Limit(items []string, n int) []string {
	if len(items) <= n {
		return items
	}
	return append(append([]string{}, items[:n]...), fmt.Sprintf("… and %d more", len(items)-n))
}

// BugError is an unexpected failure (a panic): a bug in the tool.
type BugError struct {
	Value any
	Stack string
}

func (e *BugError) Error() string { return fmt.Sprintf("unexpected error: %v", e.Value) }

var envVar = regexp.MustCompile(`%([^%]+)%`)

// CleanPath normalizes a typed or dropped path: terminals often wrap dropped
// paths in quotes; %VAR% and ~ are expanded.
func CleanPath(raw string) string {
	text := strings.TrimSpace(raw)
	if len(text) >= 2 && text[0] == text[len(text)-1] && (text[0] == '"' || text[0] == '\'') {
		text = strings.TrimSpace(text[1 : len(text)-1])
	}
	text = envVar.ReplaceAllStringFunc(text, func(m string) string {
		if value, ok := os.LookupEnv(m[1 : len(m)-1]); ok {
			return value
		}
		return m
	})
	if text == "~" || strings.HasPrefix(text, "~/") || strings.HasPrefix(text, `~\`) {
		if home, err := os.UserHomeDir(); err == nil {
			text = home + text[1:]
		}
	}
	if text == "" {
		return ""
	}
	return filepath.Clean(text)
}
