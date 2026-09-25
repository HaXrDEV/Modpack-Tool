// Command modpack-tool is HaXr's Modpack Tool: a release assistant for
// packwiz modpacks. Run it without arguments for the dashboard, or with a
// command such as `status` or `build`; `--help` lists them.
package main

import (
	"os"

	"github.com/charmbracelet/x/term"

	"github.com/HaXrDEV/Modpack-Tool/internal/app"
	"github.com/HaXrDEV/Modpack-Tool/internal/config"
	"github.com/HaXrDEV/Modpack-Tool/internal/tui"
)

func main() {
	interactive := term.IsTerminal(os.Stdin.Fd()) && term.IsTerminal(os.Stdout.Fd())
	enableVirtualTerminal()
	code := app.Main(app.Options{
		Args:        os.Args[1:],
		Stdin:       os.Stdin,
		Stdout:      os.Stdout,
		Interactive: interactive,
		Dashboard: func(cfg *config.Config, root string) error {
			return tui.Run(cfg, root)
		},
	})
	if code != app.ExitOK {
		pauseIfOwnConsole()
	}
	os.Exit(code)
}
