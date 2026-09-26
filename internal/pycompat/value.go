package pycompat

import (
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
	"time"
)

// Truthy is Python's bool(value) for decoded TOML, YAML and JSON values.
func Truthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case int:
		return v != 0
	case int64:
		return v != 0
	case uint64:
		return v != 0
	case float64:
		return v != 0
	case []any:
		return len(v) > 0
	case map[string]any:
		return len(v) > 0
	case Object:
		return len(v) > 0
	}
	return true
}

// Str is Python's str(value).
func Str(value any) string {
	if s, ok := value.(string); ok {
		return s
	}
	return Repr(value)
}

// Or is Python's `value or fallback` followed by str().
func Or(value any, fallback string) string {
	if Truthy(value) {
		return Str(value)
	}
	return fallback
}

// Repr is Python's repr(value), close enough for messages and the rare
// non-string value that ends up in text.
func Repr(value any) string {
	switch v := value.(type) {
	case nil:
		return "None"
	case bool:
		if v {
			return "True"
		}
		return "False"
	case string:
		return "'" + strings.ReplaceAll(strings.ReplaceAll(v, `\`, `\\`), "'", `\'`) + "'"
	case int:
		return strconv.Itoa(v)
	case int64:
		return strconv.FormatInt(v, 10)
	case uint64:
		return strconv.FormatUint(v, 10)
	case float64:
		return floatRepr(v)
	case time.Time:
		if v.Hour() == 0 && v.Minute() == 0 && v.Second() == 0 && v.Nanosecond() == 0 {
			return v.Format("2006-01-02")
		}
		return v.Format("2006-01-02 15:04:05")
	case []any:
		items := make([]string, len(v))
		for i, item := range v {
			items[i] = Repr(item)
		}
		return "[" + strings.Join(items, ", ") + "]"
	case map[string]any:
		keys := make([]string, 0, len(v))
		for key := range v {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		items := make([]string, len(keys))
		for i, key := range keys {
			items[i] = Repr(key) + ": " + Repr(v[key])
		}
		return "{" + strings.Join(items, ", ") + "}"
	case Object:
		items := make([]string, len(v))
		for i, m := range v {
			items[i] = Repr(m.Key) + ": " + Repr(m.Value)
		}
		return "{" + strings.Join(items, ", ") + "}"
	}
	return fmt.Sprint(value)
}

// floatRepr is Python's repr(float): the shortest round-trip form, in
// scientific notation below 1e-4 or from 1e16.
func floatRepr(f float64) string {
	switch {
	case math.IsInf(f, 1):
		return "inf"
	case math.IsInf(f, -1):
		return "-inf"
	case math.IsNaN(f):
		return "nan"
	}
	abs := math.Abs(f)
	if abs != 0 && (abs < 1e-4 || abs >= 1e16) {
		s := strconv.FormatFloat(f, 'e', -1, 64)
		mantissa, exponent, _ := strings.Cut(s, "e")
		sign := exponent[:1]
		digits := strings.TrimLeft(exponent[1:], "0")
		if len(digits) < 2 {
			digits = strings.Repeat("0", 2-len(digits)) + digits
		}
		return mantissa + "e" + sign + digits
	}
	s := strconv.FormatFloat(f, 'f', -1, 64)
	if !strings.Contains(s, ".") {
		s += ".0"
	}
	return s
}
