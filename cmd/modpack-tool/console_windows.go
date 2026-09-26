package main

import (
	"bufio"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"unsafe"

	"golang.org/x/sys/windows"

	"github.com/HaXrDEV/Modpack-Tool/internal/app"
	"github.com/HaXrDEV/Modpack-Tool/internal/proc"
)

var getConsoleProcessList = windows.NewLazySystemDLL("kernel32.dll").NewProc("GetConsoleProcessList")

// enableVirtualTerminal lets the classic console show colors (escape codes).
func enableVirtualTerminal() {
	handle := windows.Handle(os.Stdout.Fd())
	var mode uint32
	if windows.GetConsoleMode(handle, &mode) == nil {
		windows.SetConsoleMode(handle, mode|windows.ENABLE_VIRTUAL_TERMINAL_PROCESSING)
	}
}

// pauseIfOwnConsole keeps the window open after an error when the tool was
// started by double-click (the console belongs to this process alone).
func pauseIfOwnConsole() {
	processes := make([]uint32, 4)
	count, _, _ := getConsoleProcessList.Call(uintptr(unsafe.Pointer(&processes[0])), uintptr(len(processes)))
	if count != 1 {
		return
	}
	fmt.Print("Press Enter to close this window...")
	bufio.NewReader(os.Stdin).ReadString('\n')
}

// withoutConsole runs when the dashboard was asked for but there is no
// Windows console. In Git Bash's own window (mintty) the tool restarts
// itself through winpty, which gives it one.
func withoutConsole() (int, bool) {
	if !isMSYSPty(os.Stdin) || !isMSYSPty(os.Stdout) || os.Getenv("MODPACK_TOOL_WINPTY") != "" {
		return 0, false
	}
	winpty, err := proc.LookPath("winpty")
	self, selfErr := os.Executable()
	if err != nil || selfErr != nil {
		fmt.Println("The dashboard needs a Windows console, and Git Bash's own window isn't one.")
		fmt.Println("Run modpack-tool in Windows Terminal, PowerShell or cmd (or run a command such as 'modpack-tool status').")
		return app.ExitUsage, true
	}
	cmd := exec.Command(winpty, append([]string{self}, os.Args[1:]...)...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	cmd.Env = append(os.Environ(), "MODPACK_TOOL_WINPTY=1")
	if err := cmd.Run(); err != nil {
		var exit *exec.ExitError
		if errors.As(err, &exit) {
			return exit.ExitCode(), true
		}
		return 0, false
	}
	return app.ExitOK, true
}

// isMSYSPty reports whether f is an MSYS/Cygwin pty pipe, which is what
// programs started from Git Bash's own window get instead of a console.
func isMSYSPty(f *os.File) bool {
	handle := windows.Handle(f.Fd())
	if kind, err := windows.GetFileType(handle); err != nil || kind != windows.FILE_TYPE_PIPE {
		return false
	}
	var info struct {
		Length uint32
		Name   [512]uint16
	}
	if err := windows.GetFileInformationByHandleEx(handle, windows.FileNameInfo, (*byte)(unsafe.Pointer(&info)), uint32(unsafe.Sizeof(info))); err != nil {
		return false
	}
	return isMSYSPipeName(windows.UTF16ToString(info.Name[:min(int(info.Length/2), len(info.Name))]))
}

// isMSYSPipeName matches pty pipe names such as \msys-1888ae32e00d56aa-pty0-from-master.
func isMSYSPipeName(name string) bool {
	parts := strings.Split(name, "-")
	if len(parts) < 5 {
		return false
	}
	switch strings.TrimPrefix(parts[0], `\Device\NamedPipe`) {
	case `\msys`, `\cygwin`:
	default:
		return false
	}
	return parts[1] != "" && strings.HasPrefix(parts[2], "pty") && (parts[3] == "from" || parts[3] == "to") && parts[4] == "master"
}
