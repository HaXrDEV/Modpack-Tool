package tui

import (
	"image/color"
	"strings"

	"charm.land/lipgloss/v2"
	"github.com/charmbracelet/x/ansi"
)

// wrap word-wraps text to width.
func wrap(text string, width int) string { return ansi.Wrap(text, max(10, width), "") }

// truncate shortens a line to width with an ellipsis.
func truncate(text string, width int) string { return ansi.Truncate(text, max(1, width), "…") }

func lipHeight(s string) int { return lipgloss.Height(s) }

// box draws a bordered panel with a title and an optional counter in the top
// border: ╭ Title ───── 2 of 5 ╮.
func box(t *Theme, border color.Color, title, counter, body string, width int) string {
	b := t.G.Border
	line := lipgloss.NewStyle().Foreground(border)
	inner := max(4, width-4)
	right := ""
	if counter != "" {
		right = " " + t.Faint.Render(counter) + " "
	}
	heading := " " + t.Bold.Render(truncate(title, max(1, inner-lipgloss.Width(right)-2))) + " "
	fill := width - lipgloss.Width(b.TopLeft) - lipgloss.Width(heading) - lipgloss.Width(right) - lipgloss.Width(b.TopRight)
	rows := []string{line.Render(b.TopLeft) + heading + line.Render(strings.Repeat(b.Top, max(0, fill))) + right + line.Render(b.TopRight)}
	for _, row := range strings.Split(body, "\n") {
		row = truncate(row, inner)
		pad := inner - lipgloss.Width(row)
		rows = append(rows, line.Render(b.Left)+" "+row+strings.Repeat(" ", max(0, pad))+" "+line.Render(b.Right))
	}
	rows = append(rows, line.Render(b.BottomLeft+strings.Repeat(b.Bottom, max(0, width-lipgloss.Width(b.BottomLeft)-lipgloss.Width(b.BottomRight)))+b.BottomRight))
	return strings.Join(rows, "\n")
}

// bottom keeps the last height lines of text (the run screen is anchored to
// the bottom).
func bottom(lines []string, height int) []string {
	if len(lines) > height {
		return lines[len(lines)-height:]
	}
	return lines
}

// spread puts left and right text on one line of the given width.
func spread(left, right string, width int) string {
	gap := width - lipgloss.Width(left) - lipgloss.Width(right)
	if gap < 1 {
		return truncate(left+" "+right, width)
	}
	return left + strings.Repeat(" ", gap) + right
}
