// Package files writes files so that a crash or a killed process never leaves
// a half-written one behind.
package files

import (
	"errors"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"time"
)

// WriteAtomic writes data to a temporary file next to path, then renames it
// over path.
func WriteAtomic(path string, data []byte) error {
	return WriteAtomicFrom(path, func(w io.Writer) error {
		_, err := w.Write(data)
		return err
	})
}

// WriteAtomicFrom is WriteAtomic for content produced by write.
func WriteAtomicFrom(path string, write func(io.Writer) error) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".*.tmp")
	if err != nil {
		return err
	}
	name := tmp.Name()
	defer os.Remove(name) // A no-op once the rename succeeded.
	if err := write(tmp); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return Rename(name, path)
}

// Rename is os.Rename, retried for a few seconds on Windows, where antivirus
// scanners and indexers briefly hold new files open.
func Rename(from, to string) error {
	err := os.Rename(from, to)
	if err == nil || runtime.GOOS != "windows" {
		return err
	}
	for delay := 50 * time.Millisecond; delay <= 1600*time.Millisecond; delay *= 2 {
		time.Sleep(delay)
		if err = os.Rename(from, to); err == nil {
			return nil
		}
	}
	return err
}

// Exists reports whether path exists (as a file or a folder).
func Exists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

// IsFile reports whether path is an existing regular file.
func IsFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular()
}

// IsDir reports whether path is an existing folder.
func IsDir(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}

// ReadIfExists returns the file's bytes, or nil and no error when it doesn't exist.
func ReadIfExists(path string) ([]byte, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	return data, err
}
