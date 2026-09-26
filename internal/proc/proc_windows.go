package proc

import (
	"os/exec"
	"syscall"

	"golang.org/x/sys/windows"
)

// hide starts the command with its own hidden console, so it can't read the
// keyboard, change the console's modes or receive the console's Ctrl+C.
func hide(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: windows.CREATE_NO_WINDOW}
}
