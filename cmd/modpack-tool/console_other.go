//go:build !windows

package main

func enableVirtualTerminal() {}

func pauseIfOwnConsole() {}

func withoutConsole() (int, bool) { return 0, false }
