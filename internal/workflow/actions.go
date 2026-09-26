package workflow

import (
	"context"
	"flag"
	"fmt"
	"strings"
)

// Args are the command-line arguments of the actions. The dashboard runs
// actions with empty Args, so everything is asked for.
type Args struct {
	Since      string
	SkipServer bool
	NoReview   bool
	DryRun     bool
	Version    string // new-version's VERSION.
	Minecraft  string // migrate's MINECRAFT.
}

// Action is one entry of the command table that drives the dashboard, the
// subcommands and the help, so the three can't drift apart.
type Action struct {
	Key     string // Dashboard key.
	Name    string // Subcommand.
	Label   string
	Summary string // Dashboard hint.
	Help    string // Subcommand help.
	Arg     string // The optional positional argument, e.g. "VERSION".
	flags   func(*flag.FlagSet, *Args)
	arg     func(*Args, string) // The optional positional argument.
	Run     func(context.Context, *Env, Args) error
}

func sinceFlag(fs *flag.FlagSet, a *Args) {
	fs.StringVar(&a.Since, "since", "", "compare against this tag or commit instead of the last release")
}

// Actions are the tool's actions, in dashboard order.
var Actions = []Action{
	{Key: "1", Name: "update", Label: "Update mods", Summary: "packwiz update, then the alpha guard",
		Help: "packwiz update --all, with an alpha guard and re-enable offers",
		Run:  func(ctx context.Context, env *Env, _ Args) error { return UpdateMods(ctx, env) }},
	{Key: "2", Name: "new-version", Label: "New version", Summary: "bump or rename the version",
		Help: "bump the version (rename it if unreleased) and create its changelog", Arg: "VERSION",
		arg: func(a *Args, value string) { a.Version = value },
		Run: func(ctx context.Context, env *Env, a Args) error {
			_, err := NewVersion(ctx, env, "", a.Version)
			return err
		}},
	{Key: "3", Name: "draft", Label: "Draft changelog", Summary: "fill sections from the changes",
		Help: "fill changelog sections from the changes since the last release", flags: sinceFlag,
		Run: func(ctx context.Context, env *Env, a Args) error {
			_, err := Draft(ctx, env, a.Since, false)
			return err
		}},
	{Key: "4", Name: "build", Label: "Build release", Summary: "record, notes and packs",
		Help: "release record + notes, pack files, CurseForge/Modrinth/server packs",
		flags: func(fs *flag.FlagSet, a *Args) {
			sinceFlag(fs, a)
			fs.BoolVar(&a.SkipServer, "skip-server", false, "don't build the server pack")
			fs.BoolVar(&a.NoReview, "no-review", false, "don't offer to open the changelog first")
		},
		Run: func(ctx context.Context, env *Env, a Args) error {
			_, err := Build(ctx, env, a.Since, a.SkipServer, !a.NoReview)
			return err
		}},
	{Key: "5", Name: "publish", Label: "Publish", Summary: "commit, push, GitHub release",
		Help:  "commit, push and create the GitHub release (asks before each step)",
		flags: func(fs *flag.FlagSet, a *Args) { fs.BoolVar(&a.DryRun, "dry-run", false, "only show what would run") },
		Run:   func(ctx context.Context, env *Env, a Args) error { return Publish(ctx, env, a.DryRun) }},
	{Key: "6", Name: "migrate", Label: "Migrate Minecraft", Summary: "move to another Minecraft version",
		Help: "move to another Minecraft version, disable incompatible mods", Arg: "MINECRAFT",
		arg: func(a *Args, value string) { a.Minecraft = value },
		Run: func(ctx context.Context, env *Env, a Args) error { return Migrate(ctx, env, a.Minecraft) }},
	{Key: "7", Name: "check", Label: "Check pack", Summary: "sides, pins, unused libraries",
		Help: "unused libraries, invalid sides, disabled and pinned mods",
		Run:  func(ctx context.Context, env *Env, _ Args) error { return Check(ctx, env) }},
	{Key: "8", Name: "changes", Label: "View changes", Summary: "everything since the last release",
		Help: "show everything that changed since the last release", flags: sinceFlag,
		Run: func(ctx context.Context, env *Env, a Args) error {
			report, err := ChangesReport(ctx, env, a.Since)
			if err != nil {
				return err
			}
			env.UI.Info(strings.Join(report, "\n"))
			return nil
		}},
}

// ActionByName finds an action by subcommand name or dashboard key.
func ActionByName(name string) (Action, bool) {
	for _, a := range Actions {
		if a.Name == name || a.Key == name {
			return a, true
		}
	}
	return Action{}, false
}

// ParseArgs reads an action's command-line arguments.
func (a Action) ParseArgs(args []string) (Args, error) {
	var parsed Args
	fs := flag.NewFlagSet(a.Name, flag.ContinueOnError)
	fs.SetOutput(new(strings.Builder))
	if a.flags != nil {
		a.flags(fs, &parsed)
	}
	if err := fs.Parse(args); err != nil {
		return parsed, fmt.Errorf("%s: %w", a.Name, err)
	}
	rest := fs.Args()
	if len(rest) > 0 && a.arg != nil {
		a.arg(&parsed, rest[0])
		rest = rest[1:]
		// Flags may follow the positional argument too.
		if err := fs.Parse(rest); err != nil {
			return parsed, fmt.Errorf("%s: %w", a.Name, err)
		}
		rest = fs.Args()
	}
	if len(rest) > 0 {
		return parsed, fmt.Errorf("%s: unexpected argument %q", a.Name, rest[0])
	}
	return parsed, nil
}

// FlagHelp lists an action's flags for the help text.
func (a Action) FlagHelp() []string {
	if a.flags == nil {
		return nil
	}
	fs := flag.NewFlagSet(a.Name, flag.ContinueOnError)
	var args Args
	a.flags(fs, &args)
	var lines []string
	fs.VisitAll(func(f *flag.Flag) {
		name := "--" + f.Name
		if f.Name == "since" {
			name += " REF"
		}
		lines = append(lines, fmt.Sprintf("%-16s %s", name, f.Usage))
	})
	return lines
}
