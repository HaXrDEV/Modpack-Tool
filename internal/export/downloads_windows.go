package export

import "golang.org/x/sys/windows"

// downloadsDir is the user's Downloads folder (wherever it was moved to).
func downloadsDir() (string, error) {
	return windows.KnownFolderPath(windows.FOLDERID_Downloads, 0)
}
