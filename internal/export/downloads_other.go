//go:build !windows

package export

import (
	"os"
	"path/filepath"
)

// downloadsDir is the user's Downloads folder.
func downloadsDir() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, "Downloads"), nil
}
