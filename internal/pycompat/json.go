package pycompat

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
)

// NoIndent makes Dumps write everything on one line.
const NoIndent = -1

// Dumps is Python's json.dumps(value, indent=indent, ensure_ascii=ascii):
// ", " and ": " separators on one line, "," plus line breaks when indented,
// and \uXXXX escapes (lowercase hex) for non-ASCII text when ascii is set.
// Values are encoded with encoding/json first, so structs keep their field
// order and maps are sorted by key; use Object where the order must be kept.
func Dumps(value any, indent int, ascii bool) ([]byte, error) {
	var raw bytes.Buffer
	encoder := json.NewEncoder(&raw)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return nil, err
	}
	decoder := json.NewDecoder(&raw)
	decoder.UseNumber()
	p := &printer{in: decoder, indent: indent, ascii: ascii}
	if err := p.value(0); err != nil {
		return nil, err
	}
	return p.out.Bytes(), nil
}

type printer struct {
	in     *json.Decoder
	out    bytes.Buffer
	indent int
	ascii  bool
}

func (p *printer) value(level int) error {
	token, err := p.in.Token()
	if err != nil {
		return err
	}
	switch t := token.(type) {
	case json.Delim:
		p.out.WriteByte(byte(t))
		count := 0
		for p.in.More() {
			if count > 0 {
				p.out.WriteByte(',')
				if p.indent < 0 {
					p.out.WriteByte(' ')
				}
			}
			p.newline(level + 1)
			if t == '{' {
				key, err := p.in.Token()
				if err != nil {
					return err
				}
				p.str(key.(string))
				p.out.WriteString(": ")
			}
			if err := p.value(level + 1); err != nil {
				return err
			}
			count++
		}
		closing, err := p.in.Token()
		if err != nil {
			return err
		}
		if count > 0 {
			p.newline(level)
		}
		p.out.WriteByte(byte(closing.(json.Delim)))
	case string:
		p.str(t)
	case json.Number:
		p.out.WriteString(t.String())
	case bool:
		fmt.Fprint(&p.out, t)
	case nil:
		p.out.WriteString("null")
	}
	return nil
}

func (p *printer) newline(level int) {
	if p.indent < 0 {
		return
	}
	p.out.WriteByte('\n')
	for i := 0; i < level*p.indent; i++ {
		p.out.WriteByte(' ')
	}
}

func (p *printer) str(s string) {
	p.out.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			p.out.WriteString(`\"`)
		case '\\':
			p.out.WriteString(`\\`)
		case '\n':
			p.out.WriteString(`\n`)
		case '\r':
			p.out.WriteString(`\r`)
		case '\t':
			p.out.WriteString(`\t`)
		case '\b':
			p.out.WriteString(`\b`)
		case '\f':
			p.out.WriteString(`\f`)
		default:
			switch {
			case r < 0x20 || (p.ascii && r > 0x7e && r < 0x10000):
				fmt.Fprintf(&p.out, `\u%04x`, r)
			case p.ascii && r >= 0x10000:
				r -= 0x10000
				fmt.Fprintf(&p.out, `\u%04x\u%04x`, 0xd800+(r>>10), 0xdc00+(r&0x3ff))
			default:
				p.out.WriteRune(r)
			}
		}
	}
	p.out.WriteByte('"')
}

// Object is a JSON object that keeps its key order, as Python dicts do.
type Object []Member

// Member is one key of an Object.
type Member struct {
	Key   string
	Value any
}

// Get returns the value of key and whether it is present.
func (o Object) Get(key string) (any, bool) {
	for _, m := range o {
		if m.Key == key {
			return m.Value, true
		}
	}
	return nil, false
}

// Set replaces the value of key, or appends the key when it is new.
func (o *Object) Set(key string, value any) {
	for i := range *o {
		if (*o)[i].Key == key {
			(*o)[i].Value = value
			return
		}
	}
	*o = append(*o, Member{key, value})
}

// MarshalJSON writes the members in order.
func (o Object) MarshalJSON() ([]byte, error) {
	var buf bytes.Buffer
	buf.WriteByte('{')
	for i, m := range o {
		if i > 0 {
			buf.WriteByte(',')
		}
		key, err := json.Marshal(m.Key)
		if err != nil {
			return nil, err
		}
		value, err := json.Marshal(m.Value)
		if err != nil {
			return nil, err
		}
		buf.Write(key)
		buf.WriteByte(':')
		buf.Write(value)
	}
	buf.WriteByte('}')
	return buf.Bytes(), nil
}

// Loads decodes JSON keeping the key order of objects (as Object) and the
// exact text of numbers (as json.Number).
func Loads(data []byte) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	value, err := loadValue(decoder)
	if err != nil {
		return nil, err
	}
	if _, err := decoder.Token(); err != io.EOF {
		return nil, fmt.Errorf("extra data after the JSON value")
	}
	return value, nil
}

func loadValue(decoder *json.Decoder) (any, error) {
	token, err := decoder.Token()
	if err != nil {
		return nil, err
	}
	delim, ok := token.(json.Delim)
	if !ok {
		return token, nil
	}
	if delim == '[' {
		list := []any{}
		for decoder.More() {
			item, err := loadValue(decoder)
			if err != nil {
				return nil, err
			}
			list = append(list, item)
		}
		_, err := decoder.Token()
		return list, err
	}
	object := Object{}
	for decoder.More() {
		key, err := decoder.Token()
		if err != nil {
			return nil, err
		}
		value, err := loadValue(decoder)
		if err != nil {
			return nil, err
		}
		object.Set(key.(string), value)
	}
	_, err = decoder.Token()
	return object, err
}
