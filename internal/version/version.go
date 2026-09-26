// Package version parses pack versions in both schemes the packs use.
//
// Legacy versions follow PEP 440 ("4.11.1", "2.0.0.pre6", "4.12.0-beta.1").
// The MC-prefixed scheme embeds the Minecraft version: "26.2-1.0", "26.2-1.6",
// pre-releases "26.2-1.0-beta.1"; its release part resets for every Minecraft
// version. Everything here is shape-driven and never fails, so a malformed
// version can't stop a workflow.
//
// The wiki's changelog renderer (CrismPack/Wiki docs/.vitepress/changelog.mjs)
// mirrors the ordering and anchor rules, so keep the two in sync.
package version

import (
	"cmp"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/HaXrDEV/Modpack-Tool/internal/pycompat"
)

// Pre-release ranks: dev < alpha < beta < rc < final.
var preRanks = map[string]int{"dev": 0, "a": 1, "alpha": 1, "b": 2, "beta": 2, "rc": 3}

var finalPre = [2]int{9, 0}

var (
	// "4.1.1a" means post-release 1 (a=1, b=2, ...), like the old changelog
	// factory's normalize_version.
	letterSuffix = regexp.MustCompile(`^(\d+\.\d+\.\d+)([a-zA-Z])$`)
	// A trailing pre-release tag on an MC-scheme version: "26.2-1.0-beta.1".
	preTag = regexp.MustCompile(`(?i)^(.+?)[-_.](alpha|beta|rc)[.\-_]?(\d+)?$`)
	// The whole MC-prefixed scheme, optionally with a pre-release tag.
	mcScheme = regexp.MustCompile(`(?i)^\d+(\.\d+)*-\d+(\.\d+)*(-(alpha|beta|rc)\.?\d*)?$`)
	// packaging's VERSION_PATTERN (PEP 440), written without the verbose flag
	// that Go's regexp lacks.
	pep440 = regexp.MustCompile(`(?i)^\s*v?(?:(?:(?P<epoch>[0-9]+)!)?(?P<release>[0-9]+(?:\.[0-9]+)*)` +
		`(?P<pre>[._-]?(?P<pre_l>alpha|a|beta|b|preview|pre|c|rc)[._-]?(?P<pre_n>[0-9]+)?)?` +
		`(?P<post>(?:-(?P<post_n1>[0-9]+))|(?:[._-]?(?P<post_l>post|rev|r)[._-]?(?P<post_n2>[0-9]+)?))?` +
		`(?P<dev>[._-]?(?P<dev_l>dev)[._-]?(?P<dev_n>[0-9]+)?)?)` +
		`(?:\+(?P<local>[a-z0-9]+(?:[._-][a-z0-9]+)*))?\s*$`)
)

var letterNormalization = map[string]string{
	"alpha": "a", "beta": "b", "c": "rc", "pre": "rc", "preview": "rc", "rev": "post", "r": "post",
}

// Key is a total-order sort key for any version string. Kind 1 is numeric
// (PEP 440 or the MC scheme); kind 0 is the fallback for unparseable strings,
// which sort below all numeric ones. For legacy versions Main is the PEP 440
// release; for MC-scheme versions Main is the Minecraft part and Release the
// per-Minecraft release.
type Key struct {
	Kind    int
	Main    []int
	Release []int
	Pre     [2]int
	Post    int
	Text    string // The lowercased version, the final tie-breaker.
}

// Compare orders keys: -1, 0 or +1. The number lists compare like Python
// tuples, element by element and then shorter first, as slices.Compare does.
func Compare(a, b Key) int {
	return cmp.Or(cmp.Compare(a.Kind, b.Kind), slices.Compare(a.Main, b.Main), slices.Compare(a.Release, b.Release),
		slices.Compare(a.Pre[:], b.Pre[:]), cmp.Compare(a.Post, b.Post), strings.Compare(a.Text, b.Text))
}

// Less reports whether version a sorts before version b.
func Less(a, b string) bool { return Compare(ParseKey(a), ParseKey(b)) < 0 }

// IsMCPrefixed reports whether the version follows the "<mc>-<release>" scheme.
func IsMCPrefixed(v string) bool {
	return mcScheme.MatchString(pycompat.Strip(v))
}

// dottedInts parses "26.2" into [26 2]; nil when a part isn't a number.
func dottedInts(text string) []int {
	var values []int
	for _, part := range strings.Split(text, ".") {
		if !pycompat.IsDigits(part) {
			return nil
		}
		n, err := strconv.Atoi(part)
		if err != nil {
			return nil
		}
		values = append(values, n)
	}
	return values
}

// splitPreTag splits off a trailing pre-release tag: (base, (rank, number)).
func splitPreTag(text string) (string, [2]int) {
	m := preTag.FindStringSubmatch(text)
	if m == nil {
		return text, finalPre
	}
	number := 0
	if m[3] != "" {
		number, _ = strconv.Atoi(m[3])
	}
	return m[1], [2]int{preRanks[strings.ToLower(m[2])], number}
}

// pep440Version is the part of packaging.Version the sort key uses.
type pep440Version struct {
	release []int
	pre     *preRelease // nil when there is none
	post    *int
	dev     *int
}

type preRelease struct {
	letter string
	number int
}

func parsePEP440(text string) (pep440Version, bool) {
	m := pep440.FindStringSubmatch(text)
	if m == nil {
		return pep440Version{}, false
	}
	group := func(name string) string { return m[pep440.SubexpIndex(name)] }
	var v pep440Version
	for _, part := range strings.Split(group("release"), ".") {
		n, err := strconv.Atoi(part)
		if err != nil {
			return pep440Version{}, false
		}
		v.release = append(v.release, n)
	}
	if letter, number, ok := letterVersion(group("pre_l"), group("pre_n")); ok {
		v.pre = &preRelease{letter, number}
	}
	postNumber := group("post_n1")
	if postNumber == "" {
		postNumber = group("post_n2")
	}
	if _, number, ok := letterVersion(group("post_l"), postNumber); ok {
		v.post = &number
	}
	if _, number, ok := letterVersion(group("dev_l"), group("dev_n")); ok {
		v.dev = &number
	}
	return v, true
}

// letterVersion is packaging's _parse_letter_version.
func letterVersion(letter, number string) (string, int, bool) {
	n, _ := strconv.Atoi(number)
	if letter != "" {
		letter = strings.ToLower(letter)
		if normal, ok := letterNormalization[letter]; ok {
			letter = normal
		}
		return letter, n, true
	}
	if number != "" { // The implicit post-release syntax, e.g. "1.0-1".
		return "post", n, true
	}
	return "", 0, false
}

// ParseKey returns the sort key of any version string.
func ParseKey(v string) Key {
	raw := pycompat.Strip(v)
	lowered := strings.ToLower(raw)

	// Legacy: PEP 440, with the historical letter-suffix rule.
	candidate := raw
	if m := letterSuffix.FindStringSubmatch(raw); m != nil {
		post := int(strings.ToLower(m[2])[0]-'a') + 1
		candidate = m[1] + ".post" + strconv.Itoa(post)
	}
	if parsed, ok := parsePEP440(candidate); ok {
		pre := finalPre
		switch {
		case parsed.pre != nil:
			rank, known := preRanks[parsed.pre.letter]
			if !known {
				rank = 3
			}
			pre = [2]int{rank, parsed.pre.number}
		case parsed.dev != nil:
			pre = [2]int{0, *parsed.dev}
		}
		post := 0
		if parsed.post != nil {
			post = *parsed.post
		}
		return Key{Kind: 1, Main: parsed.release, Release: []int{}, Pre: pre, Post: post, Text: lowered}
	}

	// MC scheme: "<mc>-<release>" with an optional "-beta.N" style tag.
	base, pre := splitPreTag(raw)
	if mc, release, ok := strings.Cut(base, "-"); ok {
		mcParts, releaseParts := dottedInts(mc), dottedInts(release)
		if mcParts != nil && releaseParts != nil {
			return Key{Kind: 1, Main: mcParts, Release: releaseParts, Pre: pre, Text: lowered}
		}
	}
	return Key{Kind: 0, Main: []int{}, Release: []int{}, Pre: finalPre, Text: lowered}
}

// IsPrerelease reports whether the version carries a dev/alpha/beta/rc tag.
func IsPrerelease(v string) bool {
	return ParseKey(v).Pre != finalPre
}

// IsPrereleaseOf reports whether pre is a pre-release of the full release
// full: "26.2-1.0-beta.1" of "26.2-1.0", "2.2.0b1" of "2.2.0".
func IsPrereleaseOf(pre, full string) bool {
	p, f := ParseKey(pre), ParseKey(full)
	return p.Kind == 1 && f.Kind == 1 && p.Pre != finalPre && f.Pre == finalPre &&
		slices.Equal(p.Main, f.Main) && slices.Equal(p.Release, f.Release) && p.Post == f.Post
}

// Anchor is the changelog heading/anchor text for a version: MC-scheme
// versions as they are, legacy ones with a "v" in front unless they contain one.
func Anchor(v string) string {
	if IsMCPrefixed(v) || strings.Contains(v, "v") {
		return v
	}
	return "v" + v
}

// bumpRelease increments the last number of a release: "1.6" -> "1.7".
func bumpRelease(release string) string {
	parts := strings.Split(release, ".")
	n, _ := strconv.Atoi(parts[len(parts)-1])
	parts[len(parts)-1] = strconv.Itoa(n + 1)
	return strings.Join(parts, ".")
}

// NextRelease suggests the next MC-scheme version; "" for other versions. A
// pre-release promotes to its stable base ("26.2-1.0-beta.1" -> "26.2-1.0"),
// anything else bumps the release ("26.2-1.6" -> "26.2-1.7").
func NextRelease(current string) string {
	current = pycompat.Strip(current)
	if !IsMCPrefixed(current) {
		return ""
	}
	base, pre := splitPreTag(current)
	if pre != finalPre {
		return base
	}
	mc, release, _ := strings.Cut(base, "-")
	return mc + "-" + bumpRelease(release)
}

// ContentKey is the content update of a Minecraft version: its first two
// numbers, so patches share their update's key ("26.1.1" -> "26.1",
// "1.21.11" -> "1.21", "26" -> "26").
func ContentKey(mc string) string {
	text := pycompat.Strip(mc)
	var numeric []string
	for _, part := range strings.Split(text, ".") {
		if !pycompat.IsDigits(part) {
			break
		}
		numeric = append(numeric, part)
	}
	if len(numeric) == 0 {
		return text
	}
	if len(numeric) > 2 {
		numeric = numeric[:2]
	}
	return strings.Join(numeric, ".")
}

// MigrationVersion suggests the pack version after migrating to targetMC.
// The prefix is the exact Minecraft version (so a patch shows, e.g.
// "26.1.1-1.2"), while the release counter follows the content update: a
// patch continues the current line, a new content update restarts at 1.0.
func MigrationVersion(targetMC, current string) string {
	target := pycompat.Strip(targetMC)
	current = pycompat.Strip(current)
	if current != "" && IsMCPrefixed(current) {
		mc, _, _ := strings.Cut(current, "-")
		if ContentKey(mc) == ContentKey(target) {
			base, _ := splitPreTag(current)
			_, release, _ := strings.Cut(base, "-")
			return target + "-" + bumpRelease(release)
		}
	}
	return target + "-1.0"
}

// MinorVersion suggests the next minor release of a legacy version
// ("4.11.1" -> "4.12.0"); "" otherwise.
func MinorVersion(current string) string {
	base, _ := splitPreTag(pycompat.Strip(current))
	parts := strings.Split(base, ".")
	if IsMCPrefixed(current) || len(parts) < 2 || !pycompat.IsDigits(parts[0]) || !pycompat.IsDigits(parts[1]) {
		return ""
	}
	minor, _ := strconv.Atoi(parts[1])
	next := []string{parts[0], strconv.Itoa(minor + 1)}
	for range parts[2:] {
		next = append(next, "0")
	}
	return strings.Join(next, ".")
}

// NextVersion suggests the release after current, for either scheme. A
// pre-release promotes to its stable base ("4.12.0-beta.1" -> "4.12.0"),
// anything else bumps its last number ("4.11.1" -> "4.11.2", "4.1.1a" ->
// "4.1.2"). "" when there is no number to bump.
func NextVersion(current string) string {
	current = pycompat.Strip(current)
	if IsMCPrefixed(current) {
		return NextRelease(current)
	}
	base, pre := splitPreTag(current)
	if pre != finalPre {
		return base
	}
	if m := letterSuffix.FindStringSubmatch(current); m != nil {
		current = m[1]
	}
	if parts := strings.Split(current, "."); !pycompat.IsDigits(parts[len(parts)-1]) {
		return ""
	}
	return bumpRelease(current)
}
