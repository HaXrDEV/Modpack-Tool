//go:build !windows

package proc

import (
	"os/exec"
	"syscall"
)

// hide starts the command in its own process group, so a Ctrl+C in the
// terminal doesn't reach it.
func hide(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
}
