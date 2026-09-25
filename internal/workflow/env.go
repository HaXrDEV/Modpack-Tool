// Package workflow holds the actions: update mods, new version, draft
// changelog, build release, publish, migrate Minecraft and check pack, plus
// the status shown on the dashboard. They are linear functions that talk to
// the user only through a ui.Session.
package workflow

import (
	"context"
	"strings"
	"time"

	"github.com/HaXrDEV/Modpack-Tool/internal/export"
	"github.com/HaXrDEV/Modpack-Tool/internal/git"
	"github.com/HaXrDEV/Modpack-Tool/internal/packwiz"
	"github.com/HaXrDEV/Modpack-Tool/internal/platform"
	"github.com/HaXrDEV/Modpack-Tool/internal/proc"
	"github.com/HaXrDEV/Modpack-Tool/internal/project"
	"github.com/HaXrDEV/Modpack-Tool/internal/ui"
)

// Env is everything a workflow works with. Tests swap in fakes.
type Env struct {
	UI      ui.Session
	Project *project.Project
	Packwiz packwiz.Runner
	Git     *git.Repo
	API     platform.API
	Store   *export.Store
	Now     func() time.Time
	// LookPath finds executables (gh for publishing).
	LookPath func(name string) (string, error)
	// RunGH runs the GitHub CLI and returns its output.
	RunGH func(ctx context.Context, dir string, args ...string) (stdout, stderr string, code int, err error)
}

// runGH is the real RunGH.
func runGH(ctx context.Context, dir string, args ...string) (string, string, int, error) {
	result, err := proc.Run(ctx, proc.Spec{Name: "gh", Args: args, Dir: dir, Changes: true, Timeout: 10 * time.Minute})
	return string(result.Stdout), string(result.Stderr), result.Code, err
}

// NewEnv wires the real collaborators for a project.
func NewEnv(session ui.Session, p *project.Project, packwizExe string, api platform.API, cacheDir string) *Env {
	repo := git.New(p.Root)
	repo.Log = session.Log
	return &Env{
		UI:       session,
		Project:  p,
		Packwiz:  &packwiz.CLI{Exe: packwizExe, PackDir: p.PackDir(), Log: session.Log},
		Git:      repo,
		API:      api,
		Store:    export.NewStore(cacheDir, api, session),
		Now:      time.Now,
		LookPath: proc.LookPath,
		RunGH:    runGH,
	}
}

func plural(n int) string {
	if n == 1 {
		return ""
	}
	return "s"
}

// quoteArg quotes a command-line argument with spaces, for showing commands.
func quoteArg(arg string) string {
	if strings.Contains(arg, " ") {
		return `"` + arg + `"`
	}
	return arg
}
