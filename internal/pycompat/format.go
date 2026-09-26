package pycompat

import (
	"fmt"
	"strings"
)

// Format fills "{name}" placeholders like Python's str.format with keyword
// arguments; "{{" and "}}" are literal braces. Format specs, conversions and
// positional fields are reported as errors.
func Format(template string, values map[string]string) (string, error) {
	var out strings.Builder
	for i := 0; i < len(template); i++ {
		c := template[i]
		switch {
		case c == '{' && strings.HasPrefix(template[i:], "{{"):
			out.WriteByte('{')
			i++
		case c == '}' && strings.HasPrefix(template[i:], "}}"):
			out.WriteByte('}')
			i++
		case c == '}':
			return "", fmt.Errorf("single '}' encountered in format string")
		case c == '{':
			end := strings.IndexByte(template[i:], '}')
			if end < 0 {
				return "", fmt.Errorf("expected '}' before end of string")
			}
			name := template[i+1 : i+end]
			if name == "" || IsDigits(name) {
				return "", fmt.Errorf("positional placeholder {%s}", name)
			}
			value, ok := values[name]
			if !ok {
				return "", fmt.Errorf("'%s'", name)
			}
			out.WriteString(value)
			i += end
		default:
			out.WriteByte(c)
		}
	}
	return out.String(), nil
}
