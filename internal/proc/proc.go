// Package proc runs the external tools (packwiz, git, gh). Every command gets
// no input, so a tool can never wait for a keypress behind the UI, and its
// output comes back as clean lines.
package proc

import (
	"bytes"
	"context"
	"errors"
	"io"
	"io/fs"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"

	"github.com/charmbracelet/x/ansi"
)

// Spec describes one command.
type Spec struct {
	Name string
	Args []string
	Dir  string
	// Output receives each output line (stdout and stderr merged) as it arrives.
	Output func(line string)
	// Changes marks commands that change files (packwiz, git commit/push, gh
	// release create). They are never killed when ctx is canceled: the caller
	// sees the cancel after the command finishes on its own.
	Changes bool
	// Timeout kills the command after this long (0: no limit).
	Timeout time.Duration
}

// Result is a finished command.
type Result struct {
	Code   int
	Stdout []byte // Without Output: stdout alone.
	Stderr []byte
	Lines  []string // With Output: every cleaned output line.
}

// Env is added to every command's environment: no credential or gh prompts
// that would hang behind the full-screen UI.
var Env = []string{"GIT_TERMINAL_PROMPT=0", "GH_PROMPT_DISABLED=1", "GIT_PAGER=cat", "PAGER=cat"}

// ErrNotFound means the executable doesn't exist.
var ErrNotFound = errors.New("executable not found")

// Run runs a command and waits for it. A non-zero exit code is not an error;
// check Result.Code.
func Run(ctx context.Context, spec Spec) (Result, error) {
	if err := ctx.Err(); err != nil {
		return Result{}, err
	}
	runCtx := ctx
	if spec.Changes {
		runCtx = context.WithoutCancel(ctx)
	}
	if spec.Timeout > 0 {
		var cancel context.CancelFunc
		runCtx, cancel = context.WithTimeout(runCtx, spec.Timeout)
		defer cancel()
	}
	cmd := exec.CommandContext(runCtx, spec.Name, spec.Args...)
	cmd.Dir = spec.Dir
	cmd.Env = append(os.Environ(), Env...)
	cmd.Stdin = nil // The null device.
	cmd.WaitDelay = 5 * time.Second
	hide(cmd)

	var result Result
	var stdout, stderr bytes.Buffer
	var lines *lineWriter
	if spec.Output != nil {
		lines = &lineWriter{emit: func(line string) {
			result.Lines = append(result.Lines, line)
			spec.Output(line)
		}}
		cmd.Stdout, cmd.Stderr = lines, lines
	} else {
		cmd.Stdout, cmd.Stderr = &stdout, &stderr
	}
	err := cmd.Run()
	if lines != nil {
		lines.Flush()
	}
	result.Stdout, result.Stderr = stdout.Bytes(), stderr.Bytes()
	if err == nil {
		return result, nil
	}
	if ctxErr := runCtx.Err(); ctxErr != nil {
		return result, ctxErr // Canceled or timed out; the process was killed.
	}
	var exitErr *exec.ExitError
	if errors.As(err, &exitErr) {
		result.Code = exitErr.ExitCode()
		return result, nil
	}
	if errors.Is(err, exec.ErrNotFound) || errors.Is(err, fs.ErrNotExist) {
		return result, ErrNotFound
	}
	return result, err
}

// lineWriter splits output into clean lines: escape codes (progress bars)
// removed, only the text after the last carriage return kept, invalid UTF-8
// replaced and trailing space trimmed.
type lineWriter struct {
	mu      sync.Mutex
	pending []byte
	emit    func(string)
}

func (w *lineWriter) Write(p []byte) (int, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.pending = append(w.pending, p...)
	for {
		i := bytes.IndexByte(w.pending, '\n')
		if i < 0 {
			return len(p), nil
		}
		w.emit(CleanLine(string(w.pending[:i])))
		w.pending = w.pending[i+1:]
	}
}

// Flush emits a final line without a line break.
func (w *lineWriter) Flush() {
	w.mu.Lock()
	defer w.mu.Unlock()
	if len(w.pending) > 0 {
		w.emit(CleanLine(string(w.pending)))
		w.pending = nil
	}
}

var _ io.Writer = (*lineWriter)(nil)

// CleanLine makes one line of tool output fit for display.
func CleanLine(line string) string {
	line = strings.ToValidUTF8(line, "�")
	line = ansi.Strip(line)
	line = strings.TrimRight(line, "\r")
	if i := strings.LastIndex(line, "\r"); i >= 0 {
		line = line[i+1:]
	}
	return strings.TrimRight(line, " \t")
}
