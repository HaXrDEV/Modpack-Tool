// Package app is the command line: it picks the pack, then runs a subcommand
// with the plain session or opens the full-screen dashboard.
package app

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/signal"
	"runtime/debug"
	"strings"

	"github.com/HaXrDEV/Modpack-Tool/internal/config"
	"github.com/HaXrDEV/Modpack-Tool/internal/fail"
	"github.com/HaXrDEV/Modpack-Tool/internal/platform"
	"github.com/HaXrDEV/Modpack-Tool/internal/project"
	"github.com/HaXrDEV/Modpack-Tool/internal/ui"
	"github.com/HaXrDEV/Modpack-Tool/internal/workflow"
)

// Exit codes.
const (
	ExitOK     = 0
	ExitFailed = 1
	ExitUsage  = 2
)

// Options are what main passes in.
type Options struct {
	Args        []string
	Stdin       io.Reader
	Stdout      io.Writer
	Interactive bool // Stdin and stdout are a terminal.
	// Dashboard opens the full-screen app; nil when it isn't available.
	Dashboard func(cfg *config.Config, root string) error
}

// Version is the tool's version, from the Go module information.
func Version() string {
	if info, ok := debug.ReadBuildInfo(); ok && info.Main.Version != "" {
		return info.Main.Version
	}
	return "(devel)"
}

// Main runs the tool and returns the exit code.
func Main(opts Options) int {
	projectPath, command, rest, err := parseGlobal(opts.Args)
	out := opts.Stdout
	if err != nil {
		fmt.Fprintln(out, err)
		fmt.Fprintln(out, "Run 'modpack-tool --help' for the commands.")
		return ExitUsage
	}
	switch command {
	case "help":
		fmt.Fprintln(out, Usage())
		return ExitOK
	case "version":
		fmt.Fprintln(out, "modpack-tool "+Version())
		return ExitOK
	}

	cfg, warning := config.Load(config.DefaultPath())
	session := ui.NewTerminalPlain(opts.Stdin, out, opts.Interactive)
	if warning != "" {
		session.Warn(warning)
	}
	root := ChooseRoot(projectPath, cfg)
	if command == "" {
		if opts.Interactive && opts.Dashboard != nil {
			if err := opts.Dashboard(cfg, root); err != nil {
				session.Error(err.Error())
				return ExitFailed
			}
			return ExitOK
		}
		command = "status"
	}

	var action workflow.Action
	var args workflow.Args
	if command != "status" {
		var ok bool
		if action, ok = workflow.ActionByName(command); !ok {
			fmt.Fprintf(out, "Unknown command %q. Run 'modpack-tool --help' for the commands.\n", command)
			return ExitUsage
		}
		if args, err = action.ParseArgs(rest); err != nil {
			fmt.Fprintln(out, err)
			return ExitUsage
		}
	} else if len(rest) > 0 {
		fmt.Fprintf(out, "status: unexpected argument %q\n", rest[0])
		return ExitUsage
	}
	if root == "" {
		session.Error("No modpack project; pass --project PATH or run the tool inside a pack folder.")
		return ExitUsage
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	env, notes, err := Open(ctx, cfg, root, session)
	if err != nil {
		session.Error(err.Error())
		return ExitUsage
	}
	for _, note := range notes {
		session.Info(note)
	}
	if env.Git.IsRepo() && !env.Git.FetchTags(ctx) {
		session.Warn("Couldn't fetch tags from GitHub; release information may be out of date.")
	}
	if command == "status" {
		for _, line := range workflow.ComputeStatus(ctx, env).Lines() {
			session.Info(line)
		}
		return ExitOK
	}
	session.Title(action.Label + ": " + env.Project.Name + " " + env.Project.Version)
	if err := Run(ctx, env, action, args); err != nil {
		Report(session, err)
		return ExitFailed
	}
	return ExitOK
}

func parseGlobal(args []string) (projectPath, command string, rest []string, err error) {
	for i := 0; i < len(args); i++ {
		arg := args[i]
		switch {
		case arg == "-h" || arg == "--help" || arg == "-help":
			return projectPath, "help", nil, nil
		case arg == "--version":
			return projectPath, "version", nil, nil
		case arg == "--project" || arg == "-project":
			if i+1 >= len(args) {
				return "", "", nil, errors.New("--project needs a folder")
			}
			projectPath = args[i+1]
			i++
		case strings.HasPrefix(arg, "--project="):
			projectPath = strings.TrimPrefix(arg, "--project=")
		case strings.HasPrefix(arg, "-"):
			return "", "", nil, fmt.Errorf("unknown option %s", arg)
		default:
			return projectPath, arg, args[i+1:], nil
		}
	}
	return projectPath, "", nil, nil
}

// ChooseRoot picks the pack: --project first, then the pack folder containing
// the current directory, then the last used one.
func ChooseRoot(projectPath string, cfg *config.Config) string {
	if projectPath != "" {
		return ui.CleanPath(projectPath)
	}
	if cwd, err := os.Getwd(); err == nil {
		if root := project.FindRoot(cwd); root != "" {
			return root
		}
	}
	return cfg.LastUsedProject
}

// Open opens a project, remembers it and wires up its environment.
func Open(ctx context.Context, cfg *config.Config, root string, session ui.Session) (*workflow.Env, []string, error) {
	p, notes, err := project.Open(root, func(question, def string) (string, error) {
		return session.Ask(ctx, question, def)
	})
	if err != nil {
		if !fail.Is(err) && !errors.Is(err, context.Canceled) {
			err = fail.Wrapf(err, "Couldn't open %s: %v", root, err)
		}
		return nil, nil, err
	}
	if err := cfg.Remember(p.Root); err != nil {
		session.Warn("Couldn't save the project list: " + err.Error())
	}
	api := platform.NewClient(cfg.CurseForgeKey())
	return workflow.NewEnv(session, p, cfg.PackwizExe(), api, config.CacheDir()), notes, nil
}

// Run runs an action; a panic becomes a BugError instead of a crash.
func Run(ctx context.Context, env *workflow.Env, action workflow.Action, args workflow.Args) (err error) {
	defer func() {
		if value := recover(); value != nil {
			err = &ui.BugError{Value: value, Stack: string(debug.Stack())}
		}
		if reloadErr := env.Project.Reload(); reloadErr != nil && err == nil {
			err = reloadErr
		}
	}()
	return action.Run(ctx, env, args)
}

// Report shows how an action ended badly, in the plain session.
func Report(session *ui.Plain, err error) {
	var bug *ui.BugError
	switch {
	case errors.Is(err, context.Canceled):
		session.Warn("Cancelled.")
	case errors.As(err, &bug):
		session.Log(bug.Stack)
		session.Error(fmt.Sprintf("Unexpected error (the stack above is a bug in the tool): %v", bug.Value))
	default:
		session.Error(err.Error())
	}
}

// Usage is the --help text.
func Usage() string {
	var b strings.Builder
	b.WriteString("HaXr's Modpack Tool " + Version() + ": a release assistant for packwiz modpacks.\n\n")
	b.WriteString("Usage: modpack-tool [--project PATH] [command]\n\n")
	b.WriteString("Without a command, the dashboard opens. The pack is --project, else the pack folder\n")
	b.WriteString("you are in, else the last one used.\n\nCommands:\n")
	fmt.Fprintf(&b, "  %-24s %s\n", "status", "show where the pack stands")
	for _, a := range workflow.Actions {
		usage := a.Name
		if a.Arg != "" {
			usage += " [" + a.Arg + "]"
		}
		flags := a.FlagHelp()
		if len(flags) > 0 {
			usage += " [options]"
		}
		fmt.Fprintf(&b, "  %-24s %s\n", usage, a.Help)
		for _, line := range flags {
			fmt.Fprintf(&b, "      %s\n", line)
		}
	}
	b.WriteString("\n" + workflow.Help(config.DefaultPath(), config.CacheDir()) + "\n")
	return b.String()
}
