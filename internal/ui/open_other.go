//go:build !windows

package ui

import (
	"os/exec"
	"runtime"
)

// OpenFile opens a file in its default app.
func OpenFile(path string) error {
	name := "xdg-open"
	if runtime.GOOS == "darwin" {
		name = "open"
	}
	return exec.Command(name, path).Start()
}
