// Package fail holds the error type for expected failures: problems whose
// message is meant for the user, shown without a stack trace.
package fail

import (
	"errors"
	"fmt"
)

// Error is an expected failure with a message for the user.
type Error struct {
	Msg string
	Err error // The underlying cause, if any.
}

func (e *Error) Error() string { return e.Msg }
func (e *Error) Unwrap() error { return e.Err }

// Errorf returns a user-facing error.
func Errorf(format string, args ...any) error {
	return &Error{Msg: fmt.Sprintf(format, args...)}
}

// Wrapf returns a user-facing error that keeps err as its cause.
func Wrapf(err error, format string, args ...any) error {
	return &Error{Msg: fmt.Sprintf(format, args...), Err: err}
}

// Is reports whether err is, or wraps, a user-facing error.
func Is(err error) bool {
	var target *Error
	return errors.As(err, &target)
}
