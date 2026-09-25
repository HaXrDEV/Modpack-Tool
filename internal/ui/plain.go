package ui

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
)

// Plain is the line-by-line session: the same messages as prefixed lines and
// prompts read from input, one line per answer (the Python tool's wording and
// input rules, so one answer script drives both).
type Plain struct {
	mu       sync.Mutex
	in       *bufio.Reader
	out      io.Writer
	color    bool
	progress bool // Draw "done/total" progress in place.
	// Open opens a file in its default app (OpenFile unless replaced).
	Open func(path string) error
	// Canceled reports whether the user pressed Ctrl+C.
	Canceled func() bool
}

// NewPlain returns a session reading answers from in and writing to out.
func NewPlain(in io.Reader, out io.Writer) *Plain {
	return &Plain{in: bufio.NewReader(in), out: out, Open: OpenFile}
}

// NewTerminalPlain is the plain session for a terminal: colors unless
// NO_COLOR is set, and progress drawn in place.
func NewTerminalPlain(in io.Reader, out io.Writer, isTerminal bool) *Plain {
	p := NewPlain(in, out)
	p.color = isTerminal && os.Getenv("NO_COLOR") == ""
	p.progress = isTerminal
	return p
}

func (p *Plain) style(code, text string) string {
	if !p.color {
		return text
	}
	return "\x1b[" + code + "m" + text + "\x1b[0m"
}

func (p *Plain) println(text string) {
	p.mu.Lock()
	defer p.mu.Unlock()
	fmt.Fprintln(p.out, text)
}

func (p *Plain) items(values []string) {
	for _, value := range values {
		if strings.HasPrefix(value, "… and ") {
			p.println(p.style("2", "    "+value))
		} else {
			p.println("    - " + value)
		}
	}
}

// Title prints a heading, as the Python tool did at the start of an action.
func (p *Plain) Title(text string) {
	p.println("")
	p.println(p.style("1", text))
}

func (p *Plain) Step(title string) Step {
	p.println(p.style("36", "» ") + title)
	return &plainStep{p: p, title: title}
}

type plainStep struct {
	p     *Plain
	title string
	drawn bool
}

func (s *plainStep) Progress(done, total int) {
	if !s.p.progress {
		return
	}
	s.p.mu.Lock()
	defer s.p.mu.Unlock()
	end := ""
	if done >= total {
		end = "\n"
	}
	fmt.Fprintf(s.p.out, "\r  %d/%d%s", done, total, end)
	s.drawn = done < total
}

func (s *plainStep) Done(detail string) {
	if s.drawn {
		s.p.println("")
	}
	if detail != "" {
		s.p.println(s.p.style("32", "✓ ") + detail)
	}
}

func (s *plainStep) Fail(err error) {
	if s.drawn {
		s.p.println("")
	}
	s.p.println(s.p.style("31", "✗ ") + s.title + ": " + err.Error())
}

func (p *Plain) Log(line string) { p.println(p.style("2", "  | ") + line) }

func (p *Plain) Info(msg string, items ...string) {
	for _, line := range strings.Split(msg, "\n") {
		if line == "" {
			p.println("")
		} else {
			p.println("  " + line)
		}
	}
	p.items(items)
}

func (p *Plain) Warn(msg string, items ...string) {
	p.println(p.style("33", "! ") + msg)
	p.items(items)
}

// Error prints a failure message.
func (p *Plain) Error(msg string) { p.println(p.style("31", "✗ ") + msg) }

func (p *Plain) Result(summary, next string) {
	if summary != "" {
		p.println(p.style("32", "✓ ") + summary)
	}
	if next != "" {
		p.println("  Next: " + next)
	}
}

// readLine reads one answer; the end of input cancels the run.
func (p *Plain) readLine(ctx context.Context, prompt string) (string, error) {
	if err := ctx.Err(); err != nil {
		return "", err
	}
	p.mu.Lock()
	fmt.Fprint(p.out, prompt)
	p.mu.Unlock()
	line, err := p.in.ReadString('\n')
	if err != nil && (line == "" || !errors.Is(err, io.EOF)) {
		p.println("")
		return "", context.Canceled
	}
	if p.Canceled != nil && p.Canceled() {
		return "", context.Canceled
	}
	return strings.TrimSpace(line), nil
}

func (p *Plain) Confirm(ctx context.Context, question string, def bool) (bool, error) {
	hint := "y/N"
	if def {
		hint = "Y/n"
	}
	for {
		answer, err := p.readLine(ctx, question+" ["+hint+"]: ")
		if err != nil {
			return false, err
		}
		switch strings.ToLower(answer) {
		case "":
			return def, nil
		case "y", "yes":
			return true, nil
		case "n", "no":
			return false, nil
		}
		p.println("  Please answer y or n.")
	}
}

func (p *Plain) Choose(ctx context.Context, question string, options []Option, def string) (string, error) {
	var listing, keys []string
	for _, o := range options {
		listing = append(listing, o.Key+" = "+o.Label)
		keys = append(keys, o.Key)
	}
	for {
		answer, err := p.readLine(ctx, fmt.Sprintf("%s (%s) [%s]: ", question, strings.Join(listing, ", "), def))
		if err != nil {
			return "", err
		}
		if answer = strings.ToLower(answer); answer == "" {
			answer = def
		}
		for _, o := range options {
			if o.Key == answer {
				return answer, nil
			}
		}
		p.println("  Please enter one of: " + strings.Join(keys, ", ") + ".")
	}
}

func (p *Plain) PickMany(ctx context.Context, question string, labels []string) ([]int, error) {
	if len(labels) == 0 {
		return nil, nil
	}
	for i, label := range labels {
		p.println(fmt.Sprintf("    %2d) %s", i+1, label))
	}
	for {
		answer, err := p.readLine(ctx, question+" (all / none / numbers like 1,3-5) [none]: ")
		if err != nil {
			return nil, err
		}
		switch answer = strings.ToLower(answer); answer {
		case "a", "all":
			all := make([]int, len(labels))
			for i := range all {
				all[i] = i
			}
			return all, nil
		case "", "n", "none":
			return nil, nil
		}
		if picked, ok := ParseNumbers(answer, len(labels)); ok {
			return picked, nil
		}
		p.println("  Please enter all, none, or item numbers.")
	}
}

// ParseNumbers reads "1,3-5" into zero-based indexes; false when the text
// isn't a valid list of item numbers.
func ParseNumbers(text string, count int) ([]int, bool) {
	picked := map[int]bool{}
	for _, part := range strings.Split(strings.ReplaceAll(text, " ", ""), ",") {
		if part == "" {
			continue
		}
		first, last, _ := strings.Cut(part, "-")
		if last == "" { // "3" and "3-" both mean item 3.
			last = first
		}
		start, err1 := strconv.Atoi(first)
		end, err2 := strconv.Atoi(last)
		if err1 != nil || err2 != nil || strings.HasPrefix(first, "+") || strings.HasPrefix(last, "+") ||
			start < 1 || start > end || end > count {
			return nil, false
		}
		for i := start - 1; i < end; i++ {
			picked[i] = true
		}
	}
	indexes := make([]int, 0, len(picked))
	for i := range picked {
		indexes = append(indexes, i)
	}
	sort.Ints(indexes)
	return indexes, true
}

func (p *Plain) Ask(ctx context.Context, question, def string) (string, error) {
	hint := ""
	if def != "" {
		hint = " [" + def + "]"
	}
	answer, err := p.readLine(ctx, question+hint+": ")
	if err != nil {
		return "", err
	}
	if answer == "" {
		return def, nil
	}
	return answer, nil
}

func (p *Plain) AskPath(ctx context.Context, question string) (string, error) {
	answer, err := p.Ask(ctx, question, "")
	return CleanPath(answer), err
}

func (p *Plain) WaitForEdit(ctx context.Context, path string) error {
	if p.Open == nil || p.Open(path) != nil {
		p.Info("Open " + path + " in your editor.")
	}
	_, err := p.Ask(ctx, "Press Enter when you've saved your edits", "")
	return err
}
