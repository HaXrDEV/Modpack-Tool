// Package tui is the full-screen terminal app: the dashboard, the run screen
// with its prompts, projects, help and the changes view (Bubble Tea v2).
package tui

import (
	"context"
	"fmt"
	"sync"
	"time"

	tea "charm.land/bubbletea/v2"

	"github.com/HaXrDEV/Modpack-Tool/internal/config"
)

// Run opens the dashboard for the pack at root ("" to choose one) and returns
// when the user quits.
func Run(cfg *config.Config, root string) error {
	ctx, cancel := context.WithCancel(context.Background())
	var runs sync.WaitGroup
	model := newApp(ctx, cfg, root, &runs)
	program := tea.NewProgram(model)
	_, err := program.Run()
	// Stop whatever still runs; commands that change files finish on their own.
	cancel()
	waited := make(chan struct{})
	go func() {
		runs.Wait()
		close(waited)
	}()
	select {
	case <-waited:
	case <-time.After(5 * time.Second):
	}
	if model.lastResult != "" {
		fmt.Println(model.lastResult)
	}
	return err
}
