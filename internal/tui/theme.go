package tui

import (
	"image/color"
	"os"
	"runtime"

	"charm.land/bubbles/v2/spinner"
	"charm.land/lipgloss/v2"
)

// Glyphs are the symbols the UI draws: Unicode where the terminal can show
// it, ASCII in the classic Windows console (which has no font fallback).
type Glyphs struct {
	OK, Fail, Warn, Cursor, Bullet string
	Checked, Unchecked             string
	Spinner                        spinner.Spinner
	Border                         lipgloss.Border
}

var unicodeGlyphs = Glyphs{OK: "✓", Fail: "✗", Warn: "!", Cursor: "›", Bullet: "·",
	Checked: "[x]", Unchecked: "[ ]", Spinner: spinner.MiniDot, Border: lipgloss.RoundedBorder()}

var asciiGlyphs = Glyphs{OK: "+", Fail: "x", Warn: "!", Cursor: ">", Bullet: "-",
	Checked: "[x]", Unchecked: "[ ]", Spinner: spinner.Line, Border: lipgloss.NormalBorder()}

// fancyTerminal reports whether the terminal draws Unicode symbols well:
// Windows Terminal, VS Code and anything that isn't Windows.
func fancyTerminal() bool {
	return runtime.GOOS != "windows" || os.Getenv("WT_SESSION") != "" || os.Getenv("TERM_PROGRAM") != ""
}

// Theme holds the colors and styles. Color only carries meaning: accent for
// focus, green/yellow/red for status, muted for secondary text.
type Theme struct {
	G                                    Glyphs
	Accent, Muted, Line, OK, Warn, Error color.Color

	Title, Bold, Faint, AccentText, OKText, WarnText, ErrorText lipgloss.Style
	Card                                                        lipgloss.Style
}

// NewTheme builds the theme for a dark or light terminal background.
func NewTheme(dark, fancy bool) *Theme {
	c := lipgloss.LightDark(dark)
	t := &Theme{
		Accent: c(lipgloss.Color("#5B4BDB"), lipgloss.Color("#A99BFF")),
		Muted:  c(lipgloss.Color("#6E6E6E"), lipgloss.Color("#8B8B8B")),
		Line:   c(lipgloss.Color("#C8C8C8"), lipgloss.Color("#444444")),
		OK:     c(lipgloss.Color("#1A7F37"), lipgloss.Color("#56D364")),
		Warn:   c(lipgloss.Color("#9A6700"), lipgloss.Color("#E3B341")),
		Error:  c(lipgloss.Color("#CF222E"), lipgloss.Color("#FF7B72")),
		G:      asciiGlyphs,
	}
	if fancy {
		t.G = unicodeGlyphs
	}
	t.Title = lipgloss.NewStyle().Bold(true)
	t.Bold = lipgloss.NewStyle().Bold(true)
	t.Faint = lipgloss.NewStyle().Foreground(t.Muted)
	t.AccentText = lipgloss.NewStyle().Foreground(t.Accent)
	t.OKText = lipgloss.NewStyle().Foreground(t.OK)
	t.WarnText = lipgloss.NewStyle().Foreground(t.Warn)
	t.ErrorText = lipgloss.NewStyle().Foreground(t.Error)
	t.Card = lipgloss.NewStyle().Border(t.G.Border).BorderForeground(t.Line).Padding(0, 1)
	return t
}
