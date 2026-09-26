// Package packwiz runs the packwiz CLI inside a pack's Packwiz folder.
package packwiz

import (
	"context"
	"errors"
	"strings"

	"github.com/HaXrDEV/Modpack-Tool/internal/fail"
	"github.com/HaXrDEV/Modpack-Tool/internal/proc"
)

// Runner is what the workflows need from packwiz; tests use a fake.
type Runner interface {
	Refresh(ctx context.Context) error
	// UpdateAll ignores exit codes: one unavailable mod shouldn't abort the rest.
	UpdateAll(ctx context.Context) error
	Pin(ctx context.Context, slug string) error
	Unpin(ctx context.Context, slug string) error
	Remove(ctx context.Context, slug string) error
	MigrateMinecraft(ctx context.Context, version string) error
	MigrateLoader(ctx context.Context, version string) error
	AddAcceptableVersion(ctx context.Context, version string) error
	RemoveAcceptableVersion(ctx context.Context, version string) error
}

// CLI runs the real packwiz executable.
type CLI struct {
	Exe     string
	PackDir string
	Log     func(line string) // Where packwiz's output goes.
}

func (c *CLI) run(ctx context.Context, echo, check bool, args ...string) error {
	log := c.Log
	if log == nil {
		log = func(string) {}
	}
	output := func(line string) {
		if echo && strings.TrimSpace(line) != "" {
			log(line)
		}
	}
	// packwiz changes files, so a cancel lets it finish instead of killing it halfway.
	result, err := proc.Run(ctx, proc.Spec{Name: c.Exe, Args: args, Dir: c.PackDir, Output: output, Changes: true})
	if errors.Is(err, proc.ErrNotFound) {
		return fail.Errorf("packwiz was not found at '%s'. Install it with "+
			"'go install github.com/packwiz/packwiz@latest' or set packwiz_exe_path in the tool's config.yml.", c.Exe)
	} else if err != nil {
		return fail.Wrapf(err, "Could not start packwiz: %v", err)
	}
	if check && result.Code != 0 {
		if !echo {
			for _, line := range result.Lines {
				log(line)
			}
		}
		return fail.Errorf("'packwiz %s' failed (exit code %d).", strings.Join(args, " "), result.Code)
	}
	return nil
}

func (c *CLI) Refresh(ctx context.Context) error { return c.run(ctx, false, true, "refresh") }
func (c *CLI) UpdateAll(ctx context.Context) error {
	return c.run(ctx, true, false, "update", "--all", "-y")
}
func (c *CLI) Pin(ctx context.Context, slug string) error {
	return c.run(ctx, false, true, "pin", slug)
}
func (c *CLI) Unpin(ctx context.Context, slug string) error {
	return c.run(ctx, false, true, "unpin", slug)
}
func (c *CLI) Remove(ctx context.Context, slug string) error {
	return c.run(ctx, true, true, "remove", slug)
}
func (c *CLI) MigrateMinecraft(ctx context.Context, version string) error {
	return c.run(ctx, true, true, "migrate", "minecraft", version, "-y")
}
func (c *CLI) MigrateLoader(ctx context.Context, version string) error {
	return c.run(ctx, true, true, "migrate", "loader", version, "-y")
}
func (c *CLI) AddAcceptableVersion(ctx context.Context, version string) error {
	return c.run(ctx, false, true, "settings", "acceptable-versions", "--add", version)
}
func (c *CLI) RemoveAcceptableVersion(ctx context.Context, version string) error {
	return c.run(ctx, false, true, "settings", "acceptable-versions", "--remove", version)
}
