// Cartograph contamination scanner for Go widgets.
//
// Stdlib-only (go/ast, go/parser): string- and comment-aware by
// construction, so a URL in a comment or a "fmt.Println" inside a string
// literal never false-positives.
//
// Usage:  go run go_scanner.go <file.go> [...]
// Reads widget.json from the working directory (if present) for the
// declared dependency list used by the unlisted_import check.
//
// Output: one JSON array on stdout. Each finding:
//
//	{"kind": "...", "file": "...", "line": N, "detail": "...", "severity": "error"|"warning"}
//
// Severity "error" findings block validation; "warning" surfaces but can be
// overridden at checkin.
package main

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

type finding struct {
	Kind     string `json:"kind"`
	File     string `json:"file"`
	Line     int    `json:"line"`
	Detail   string `json:"detail"`
	Severity string `json:"severity"`
}

var (
	urlRe = regexp.MustCompile(`https?://[^\s"']+`)
	// Placeholder hosts that are fine to hardcode anywhere.
	urlAllowRe = regexp.MustCompile(
		`https?://(localhost|127\.0\.0\.1|([\w-]+\.)*example\.(com|org|net)|[\w.-]+\.test)([/:#?]|$)`)
	ipRe = regexp.MustCompile(`\b(\d{1,3}\.){3}\d{1,3}\b`)
	// Loopback/wildcard/broadcast and version-number-shaped matches are fine.
	ipAllow    = map[string]bool{"0.0.0.0": true, "127.0.0.1": true, "255.255.255.255": true}
	absPathRe  = regexp.MustCompile(`^(/home/|/Users/|/root/|/var/|/etc/|[A-Za-z]:[/\\])`)
	credNameRe = regexp.MustCompile(`(?i)(password|passwd|secret|api_?key|auth_?token|private_?key|client_?secret)`)
)

// widgetDeps reads declared dependency module paths from widget.json in cwd.
func widgetDeps() map[string]bool {
	deps := map[string]bool{}
	raw, err := os.ReadFile("widget.json")
	if err != nil {
		return deps
	}
	var doc map[string]any
	if err := json.Unmarshal(raw, &doc); err != nil {
		return deps
	}
	// tech_stack lives at the manifest root; older manifests nested it
	// under meta. Bare top-level "dependencies" is the last resort.
	var list []any
	containers := []any{doc["tech_stack"]}
	if meta, ok := doc["meta"].(map[string]any); ok {
		containers = append(containers, meta["tech_stack"])
	}
	for _, container := range containers {
		if stack, ok := container.(map[string]any); ok {
			if l, ok := stack["dependencies"].([]any); ok {
				list = l
				break
			}
		}
	}
	if list == nil {
		if l, ok := doc["dependencies"].([]any); ok {
			list = l
		}
	}
	for _, d := range list {
		s, ok := d.(string)
		if !ok {
			continue
		}
		// "github.com/google/uuid>=1.6.0" -> "github.com/google/uuid"
		for _, sep := range []string{">=", "==", "<=", ">", "<", "@", " "} {
			if i := strings.Index(s, sep); i >= 0 {
				s = s[:i]
			}
		}
		if s != "" {
			deps[strings.ToLower(strings.TrimSpace(s))] = true
		}
	}
	return deps
}

// moduleName reads the module path from go.mod in cwd, so imports of the
// widget's own packages are never flagged as unlisted.
func moduleName() string {
	raw, err := os.ReadFile("go.mod")
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "module ") {
			return strings.TrimSpace(strings.TrimPrefix(line, "module "))
		}
	}
	return ""
}

func isSrcFile(path string) bool {
	p := filepath.ToSlash(path)
	return strings.Contains(p, "/src/") || strings.HasPrefix(p, "src/")
}

// isStdlib: stdlib import paths have no dot in their first element
// ("fmt", "net/http"); external modules do ("github.com/...").
func isStdlib(importPath string) bool {
	first := importPath
	if i := strings.Index(importPath, "/"); i >= 0 {
		first = importPath[:i]
	}
	return !strings.Contains(first, ".")
}

func main() {
	findings := []finding{}
	deps := widgetDeps()
	ownModule := strings.ToLower(moduleName())
	fset := token.NewFileSet()

	add := func(kind, file string, line int, detail, severity string) {
		findings = append(findings, finding{
			Kind: kind, File: file, Line: line,
			Detail: detail, Severity: severity,
		})
	}

	for _, path := range os.Args[1:] {
		// The engine invokes the compiled scanner as
		// `<binary> go_scanner.go <files...>` (the harness always passes
		// the scanner source path first); skip ourselves.
		if filepath.Base(path) == "go_scanner.go" {
			continue
		}
		// ParseComments so Doc fields populate for the missing-doc check.
		// Comment groups attach to nodes but expression/statement parsing
		// is unchanged, so string/comment awareness is unaffected.
		f, err := parser.ParseFile(fset, path, nil, parser.ParseComments)
		if err != nil {
			// Syntax errors are go vet/build's job; skip quietly here.
			continue
		}
		src := isSrcFile(path)
		line := func(n ast.Node) int { return fset.Position(n.Pos()).Line }
		// Contract: contamination blocks in src/, warns in tests/examples.
		// "block" (not "error") - the engine groups error-severity findings
		// whose kind has a warning header into warnings; block bypasses that.
		sev := "warning"
		if src {
			sev = "block"
		}

		// Imports: deprecated/legacy stdlib (modern-standards), then
		// unlisted external modules (all files - tests and examples
		// install the same deps as src).
		for _, imp := range f.Imports {
			p := strings.Trim(imp.Path.Value, `"`)
			switch p {
			case "io/ioutil":
				// Deprecated since Go 1.16; every ioutil function has a
				// named replacement in io or os. Block in src/.
				add("deprecated_import", path, line(imp), p, sev)
			case "math/rand":
				// math/rand/v2 (Go 1.22+) is the modern API: better
				// algorithm, cleaner surface, no global-seed footguns.
				add("legacy_rand", path, line(imp), p, "warning")
			}
			if isStdlib(p) {
				continue
			}
			lower := strings.ToLower(p)
			if ownModule != "" && (lower == ownModule ||
				strings.HasPrefix(lower, ownModule+"/")) {
				continue
			}
			listed := false
			for d := range deps {
				if lower == d || strings.HasPrefix(lower, d+"/") {
					listed = true
					break
				}
			}
			if !listed {
				add("unlisted_import", path, line(imp), p, "warning")
			}
		}

		// File-scope declarations: iterate f.Decls directly - it holds only
		// top-level decls, so function-local `var` never trips these.
		if src {
			for _, decl := range f.Decls {
				gd, ok := decl.(*ast.GenDecl)
				if !ok || (gd.Tok != token.VAR && gd.Tok != token.CONST) {
					continue
				}
				for _, spec := range gd.Specs {
					vs, ok := spec.(*ast.ValueSpec)
					if !ok {
						continue
					}
				names:
					for vi, ident := range vs.Names {
						if ident.Name == "_" {
							continue
						}
						// Sentinel errors (var ErrNotFound = errors.New(...))
						// are the idiomatic Go error contract, not state.
						if strings.HasPrefix(ident.Name, "Err") ||
							strings.HasPrefix(ident.Name, "err") {
							continue
						}
						ln := fset.Position(ident.Pos()).Line
						// Mutable package state: warning only - an exported
						// config var can be a deliberate API choice.
						// Constants are fine.
						if gd.Tok == token.VAR {
							add("top_level_var", path, ln,
								"var "+ident.Name, "warning")
						}
						// Hardcoded tunables: numeric literal initializers
						// (TIMEOUT = 30) should be parameters instead.
						if vi < len(vs.Values) {
							if lit, ok := vs.Values[vi].(*ast.BasicLit); ok &&
								(lit.Kind == token.INT || lit.Kind == token.FLOAT) &&
								lit.Value != "0" && lit.Value != "1" {
								add("hardcoded_value", path, ln,
									ident.Name+" = "+lit.Value, "warning")
								continue names
							}
						}
					}
				}
			}
		}

		// Missing doc comments on exported top-level identifiers. For a
		// widget library the exported surface IS the product, so every
		// exported func/type/var/const should be documented. A group doc
		// (on the GenDecl) covers all its specs - matches Go convention
		// and keeps enum blocks from being noisy.
		if src {
			for _, decl := range f.Decls {
				switch d := decl.(type) {
				case *ast.FuncDecl:
					if d.Name.IsExported() && d.Doc == nil &&
						(d.Recv == nil || receiverExported(d.Recv)) {
						add("missing_doc", path,
							fset.Position(d.Name.Pos()).Line,
							"func "+d.Name.Name, "warning")
					}
				case *ast.GenDecl:
					for _, spec := range d.Specs {
						switch s := spec.(type) {
						case *ast.TypeSpec:
							if s.Name.IsExported() && d.Doc == nil && s.Doc == nil {
								add("missing_doc", path,
									fset.Position(s.Name.Pos()).Line,
									"type "+s.Name.Name, "warning")
							}
						case *ast.ValueSpec:
							for _, nm := range s.Names {
								if nm.IsExported() && d.Doc == nil && s.Doc == nil {
									add("missing_doc", path,
										fset.Position(nm.Pos()).Line,
										nm.Name, "warning")
								}
							}
						}
					}
				}
			}
		}

		ast.Inspect(f, func(n ast.Node) bool {
			switch node := n.(type) {

			case *ast.CallExpr:
				name := calleeName(node)
				switch {
				case src && (name == "fmt.Print" || name == "fmt.Println" ||
					name == "fmt.Printf" || name == "print" ||
					name == "println"):
					add("print", path, line(node), name+"(...)", "error")
				case src && (name == "os.Exit" || name == "log.Fatal" ||
					name == "log.Fatalf" || name == "log.Fatalln"):
					add("exit", path, line(node), name+"(...)", "error")
				case name == "time.Sleep":
					if src {
						add("sleep", path, line(node), name+"(...)", "block")
					} else if sleepSeconds(node) > 1 {
						add("sleep", path, line(node), name+"(...)", "warning")
					}
				case src && (name == "os.Getenv" || name == "os.LookupEnv"):
					add("env_var", path, line(node), name+"(...)", "warning")
				}

			case *ast.FuncDecl:
				// panic() reachable during package init takes down every
				// consumer at import time.
				if src && node.Name.Name == "init" && node.Recv == nil {
					ast.Inspect(node.Body, func(inner ast.Node) bool {
						if c, ok := inner.(*ast.CallExpr); ok {
							if id, ok := c.Fun.(*ast.Ident); ok && id.Name == "panic" {
								add("panic_toplevel", path, line(c),
									"panic() in init()", "error")
							}
						}
						return true
					})
				}

			case *ast.InterfaceType:
				// interface{} -> any (Go 1.18+). The `any` spelling parses
				// as an Ident, so only the literal empty-interface type
				// trips this. Interfaces with methods are untouched.
				if src && (node.Methods == nil || len(node.Methods.List) == 0) {
					add("empty_interface", path, line(node),
						"interface{} - use the 'any' alias", "warning")
				}

			case *ast.GoStmt:
				// Anonymous `go func(){...}()` is the top source of leaked
				// goroutines - flag it so the author confirms a WaitGroup
				// or context governs its lifetime. Named `go worker()` is
				// left alone (less leak-prone, noisier to flag).
				if src {
					if _, ok := node.Call.Fun.(*ast.FuncLit); ok {
						add("bare_goroutine", path, line(node),
							"go func() - ensure WaitGroup/context lifecycle",
							"warning")
					}
				}

			case *ast.BinaryExpr:
				// Comparing errors with == / != breaks on wrapped errors
				// (fmt.Errorf("%w")); use errors.Is. `err == nil` is exempt
				// because neither side is a sentinel.
				if src && (node.Op == token.EQL || node.Op == token.NEQ) {
					if (isSentinelError(node.X) && !isNilIdent(node.Y)) ||
						(isSentinelError(node.Y) && !isNilIdent(node.X)) {
						add("error_equality", path, line(node),
							"compare errors with errors.Is, not "+node.Op.String(),
							"warning")
					}
				}

			case *ast.BasicLit:
				if node.Kind != token.STRING {
					return true
				}
				val := strings.Trim(node.Value, "`\"")
				if m := urlRe.FindString(val); m != "" && !urlAllowRe.MatchString(m) {
					sev := "warning"
					add("hardcoded_url", path, line(node), m, sev)
				}
				if loc := ipRe.FindStringIndex(val); loc != nil {
					m := val[loc[0]:loc[1]]
					// RE2 has no lookaround: reject matches embedded in a
					// longer dotted run (version strings like 1.2.3.4.5).
					embedded := (loc[0] > 0 && (val[loc[0]-1] == '.' || isDigit(val[loc[0]-1]))) ||
						(loc[1] < len(val) && (val[loc[1]] == '.' || isDigit(val[loc[1]])))
					if !embedded && !ipAllow[m] &&
						!strings.Contains(val, "example") && validIP(m) {
						add("hardcoded_ip", path, line(node), m, sev)
					}
				}
				if absPathRe.MatchString(val) {
					add("abs_path", path, line(node), val, sev)
				}

			case *ast.KeyValueExpr, *ast.AssignStmt, *ast.ValueSpec:
				// handled via parent traversal below
			}

			// Credential-shaped assignments: name matches a credential
			// pattern AND the value is a non-empty string literal. Covers
			// both := / = statements and var/const ValueSpecs.
			switch node := n.(type) {
			case *ast.AssignStmt:
				for i, lhs := range node.Lhs {
					id, ok := lhs.(*ast.Ident)
					if !ok || !credNameRe.MatchString(id.Name) {
						continue
					}
					if i < len(node.Rhs) {
						if lit, ok := node.Rhs[i].(*ast.BasicLit); ok &&
							lit.Kind == token.STRING &&
							len(strings.Trim(lit.Value, "`\"")) >= 6 {
							add("credential", path, fset.Position(id.Pos()).Line,
								id.Name+" = "+truncate(lit.Value, 40), sev)
						}
					}
				}
			case *ast.ValueSpec:
				for i, id := range node.Names {
					if !credNameRe.MatchString(id.Name) {
						continue
					}
					if i < len(node.Values) {
						if lit, ok := node.Values[i].(*ast.BasicLit); ok &&
							lit.Kind == token.STRING &&
							len(strings.Trim(lit.Value, "`\"")) >= 6 {
							add("credential", path, fset.Position(id.Pos()).Line,
								id.Name+" = "+truncate(lit.Value, 40), sev)
						}
					}
				}
			}
			return true
		})
	}

	out, _ := json.Marshal(findings)
	fmt.Println(string(out))
}

// receiverExported reports whether a method receiver's base type is
// exported, so we only require docs on methods that are part of the
// public surface (a method on an unexported type is internal).
func receiverExported(recv *ast.FieldList) bool {
	if recv == nil || len(recv.List) == 0 {
		return false
	}
	t := recv.List[0].Type
	if star, ok := t.(*ast.StarExpr); ok {
		t = star.X
	}
	// Generic receiver: Foo[T] -> base is the IndexExpr's X.
	if idx, ok := t.(*ast.IndexExpr); ok {
		t = idx.X
	}
	if idx, ok := t.(*ast.IndexListExpr); ok {
		t = idx.X
	}
	if id, ok := t.(*ast.Ident); ok {
		return id.IsExported()
	}
	return false
}

// isSentinelError reports whether e references an error sentinel: an
// identifier or selector whose name starts with "Err", or the common
// non-Err-prefixed sentinel io.EOF.
func isSentinelError(e ast.Expr) bool {
	switch x := e.(type) {
	case *ast.Ident:
		return strings.HasPrefix(x.Name, "Err")
	case *ast.SelectorExpr:
		if strings.HasPrefix(x.Sel.Name, "Err") {
			return true
		}
		if id, ok := x.X.(*ast.Ident); ok && id.Name == "io" && x.Sel.Name == "EOF" {
			return true
		}
	}
	return false
}

// isNilIdent reports whether e is the bare identifier nil.
func isNilIdent(e ast.Expr) bool {
	id, ok := e.(*ast.Ident)
	return ok && id.Name == "nil"
}

// sleepSeconds estimates a time.Sleep duration in seconds. Recognizes
// `N * time.Second/Minute/Hour/Millisecond` and bare `time.Second` etc.
// Returns 0 when the duration can't be determined statically (variables,
// function results) - dynamic sleeps in tests are the author's judgment.
func sleepSeconds(call *ast.CallExpr) float64 {
	if len(call.Args) != 1 {
		return 0
	}
	unit := func(e ast.Expr) float64 {
		if sel, ok := e.(*ast.SelectorExpr); ok {
			if x, ok := sel.X.(*ast.Ident); ok && x.Name == "time" {
				switch sel.Sel.Name {
				case "Nanosecond":
					return 1e-9
				case "Microsecond":
					return 1e-6
				case "Millisecond":
					return 1e-3
				case "Second":
					return 1
				case "Minute":
					return 60
				case "Hour":
					return 3600
				}
			}
		}
		return 0
	}
	switch arg := call.Args[0].(type) {
	case *ast.SelectorExpr:
		return unit(arg)
	case *ast.BinaryExpr:
		if arg.Op != token.MUL {
			return 0
		}
		for _, pair := range [][2]ast.Expr{{arg.X, arg.Y}, {arg.Y, arg.X}} {
			if lit, ok := pair[0].(*ast.BasicLit); ok &&
				(lit.Kind == token.INT || lit.Kind == token.FLOAT) {
				if u := unit(pair[1]); u > 0 {
					n := 0.0
					fmt.Sscanf(lit.Value, "%g", &n)
					return n * u
				}
			}
		}
	}
	return 0
}

// calleeName renders a call target as "pkg.Func" or "func".
func calleeName(call *ast.CallExpr) string {
	switch fun := call.Fun.(type) {
	case *ast.Ident:
		return fun.Name
	case *ast.SelectorExpr:
		if x, ok := fun.X.(*ast.Ident); ok {
			return x.Name + "." + fun.Sel.Name
		}
	}
	return ""
}

func isDigit(b byte) bool { return b >= '0' && b <= '9' }

func validIP(s string) bool {
	parts := strings.Split(s, ".")
	if len(parts) != 4 {
		return false
	}
	for _, p := range parts {
		if len(p) > 1 && p[0] == '0' {
			return false // version-string shaped, e.g. 1.02.3.4
		}
		n := 0
		for _, c := range p {
			n = n*10 + int(c-'0')
		}
		if n > 255 {
			return false
		}
	}
	return true
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}
