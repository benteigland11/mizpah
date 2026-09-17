/-
Native contamination scanner for Lean 4 widgets.

Invoked by the Lean engine as:  lean --run lean_scanner.lean <file> ...
with cwd = the widget root (so widget.json is readable for declared deps).

Outputs a JSON array of findings on the last stdout line:
  [{"kind": ..., "file": ..., "line": N, "detail": ..., "severity": ...}]
Severity is "error" (hard block), or "warning". The engine folds errors
into contamination blocks; validate_widget treats them as errors directly.

The lexer walks each file character-by-character tracking nested block
comments (/- -/ including doc comments), line comments (--), string
literals (with escapes, multi-line), interpolated strings (s!"..."), and
char literals, producing two shadow texts per line:
  code: string/char contents blanked, comments blanked  -> token checks
  str:  string contents kept, comments blanked          -> literal checks
so a banned token inside a string or comment never trips, and a hardcoded
path inside a comment never trips.
-/
import Lean.Data.Json

open Lean (Json)

def strip (s : String) : String := s.trimAscii.toString

def dropStr (s : String) (n : Nat) : String := (s.drop n).toString

/-- Character classes the lexer assigns to every input character. -/
inductive CharClass where
  | code | str | comment
  deriving BEq

structure LexState where
  commentDepth : Nat := 0
  inLineComment : Bool := false
  inString : Bool := false
  escaped : Bool := false
  deriving Inhabited

/-- Classify every char of `text`. Interpolation gaps inside s!"..{x}.."
    are rare in contamination contexts; we treat the whole literal as
    string, which errs toward fewer false positives on token checks. -/
def classify (text : String) : Array (Char × CharClass) := Id.run do
  let mut out : Array (Char × CharClass) := #[]
  let mut st : LexState := {}
  let chars := text.toList.toArray
  let mut i := 0
  while h : i < chars.size do
    let c := chars[i]
    let next : Char := if i + 1 < chars.size then chars[i + 1]! else ' '
    if st.inLineComment then
      if c == '\n' then
        st := { st with inLineComment := false }
        out := out.push (c, .code)
      else
        out := out.push (c, .comment)
      i := i + 1
    else if st.commentDepth > 0 then
      if c == '-' && next == '/' then
        out := out.push (c, .comment); out := out.push (next, .comment)
        st := { st with commentDepth := st.commentDepth - 1 }
        i := i + 2
      else if c == '/' && next == '-' then
        out := out.push (c, .comment); out := out.push (next, .comment)
        st := { st with commentDepth := st.commentDepth + 1 }
        i := i + 2
      else
        out := out.push (c, .comment); i := i + 1
    else if st.inString then
      if st.escaped then
        out := out.push (c, .str); st := { st with escaped := false }; i := i + 1
      else if c == '\\' then
        out := out.push (c, .str); st := { st with escaped := true }; i := i + 1
      else if c == '"' then
        out := out.push (c, .code); st := { st with inString := false }; i := i + 1
      else
        out := out.push (c, .str); i := i + 1
    else
      if c == '-' && next == '-' then
        out := out.push (c, .comment); out := out.push (next, .comment)
        st := { st with inLineComment := true }
        i := i + 2
      else if c == '/' && next == '-' then
        out := out.push (c, .comment); out := out.push (next, .comment)
        st := { st with commentDepth := 1 }
        i := i + 2
      else if c == '"' then
        out := out.push (c, .code); st := { st with inString := true }; i := i + 1
      else if c == '\'' then
        -- char literal: 'x' or '\n' - consume up to closing quote (max 4 chars)
        out := out.push (c, .code)
        let mut j := i + 1
        let stop := min chars.size (i + 5)
        while j < stop do
          let cj := chars[j]!
          if cj == '\'' && j > i + 1 then
            out := out.push (cj, .code); j := j + 1; break
          out := out.push (cj, .str); j := j + 1
        i := j
      else
        out := out.push (c, .code); i := i + 1
  return out

/-- Split classified chars into per-line (codeLine, strLine) pairs.
    codeLine blanks str+comment chars; strLine blanks comment chars and
    keeps code+str so quoted literals stay findable with their context. -/
def shadowLines (text : String) : Array (String × String) := Id.run do
  let mut lines : Array (String × String) := #[]
  let mut code := ""
  let mut strl := ""
  for (c, cls) in classify text do
    if c == '\n' then
      lines := lines.push (code, strl); code := ""; strl := ""
    else
      code := code.push (if cls == .code then c else ' ')
      strl := strl.push (if cls == .comment then ' ' else c)
  lines := lines.push (code, strl)
  return lines

def isIdentChar (c : Char) : Bool :=
  c.isAlphanum || c == '_' || c == '\''

/-- Does `line` contain `tok` as a standalone word (not part of an ident)? -/
def hasToken (line : String) (tok : String) : Bool := Id.run do
  let mut rest := line
  while rest.length >= tok.length do
    match rest.splitOn tok with
    | [_] => return false
    | before :: _ =>
      let idx := before.length
      let pre : Char := if idx == 0 then ' ' else rest.toList[idx - 1]!
      let postIdx := idx + tok.length
      let post : Char := if postIdx < rest.length then rest.toList[postIdx]! else ' '
      if !isIdentChar pre && !isIdentChar post then
        return true
      rest := dropStr rest (idx + tok.length)
    | [] => return false
  return false

def containsSub (line sub : String) : Bool :=
  (line.splitOn sub).length > 1

/-- First natural-number literal appearing after `marker` in `line`. -/
def numberAfter (line marker : String) : Option Nat := Id.run do
  match line.splitOn marker with
  | _ :: rest :: _ =>
    let mut digits := ""
    let mut started := false
    for c in rest.toList do
      if c.isDigit then
        digits := digits.push c; started := true
      else if started then break
      else if c == ' ' || c == '(' then pure ()
      else break
    return digits.toNat?
  | _ => return none

/-- Detect an IPv4-looking literal with at least one multi-digit octet. -/
def hasHardcodedIp (line : String) : Bool := Id.run do
  let cs := line.toList.toArray
  let mut i := 0
  while i < cs.size do
    if (cs[i]!).isDigit then
      let mut j := i
      let mut octets : Array String := #[]
      let mut cur := ""
      while j < cs.size && ((cs[j]!).isDigit || cs[j]! == '.') do
        if cs[j]! == '.' then
          octets := octets.push cur; cur := ""
        else
          cur := cur.push (cs[j]!)
        j := j + 1
      octets := octets.push cur
      if octets.size == 4 && octets.all (fun o => 1 ≤ o.length && o.length ≤ 3 && o.toNat?.isSome)
          && octets.any (fun o => o.length ≥ 2) then
        return true
      i := j + 1
    else
      i := i + 1
  return false

def urlAllowed (line : String) : Bool :=
  containsSub line "localhost" || containsSub line "127.0.0.1"
    || containsSub line "example.com" || containsSub line "example.org"
    || containsSub line "example.net" || containsSub line ".test"
    || containsSub line "schema."    || containsSub line "schemas."

def credentialNames : List String :=
  ["api_key", "apiKey", "api_secret", "secret_key", "secretKey",
   "access_token", "accessToken", "auth_token", "authToken",
   "password", "passwd", "credential"]

structure Finding where
  kind : String
  file : String
  line : Nat
  detail : String
  severity : String

def Finding.toJson (f : Finding) : Json :=
  Json.mkObj [("kind", Json.str f.kind), ("file", Json.str f.file),
              ("line", Json.num f.line), ("detail", Json.str f.detail),
              ("severity", Json.str f.severity)]

/-- Roots that never count as unlisted imports: the Lean distribution. -/
def stdlibRoots : List String := ["Init", "Std", "Lean", "Lake"]

def depBareName (dep : String) : String := Id.run do
  let mut name := ""
  for c in dep.toList do
    if c == '>' || c == '<' || c == '=' || c == '!' || c == '~' then break
    name := name.push c
  return strip name

/-- Declared dependency names from widget.json in cwd (empty if absent). -/
def declaredDeps : IO (List String) := do
  try
    let txt ← IO.FS.readFile "widget.json"
    match Json.parse txt with
    | .error _ => return []
    | .ok j =>
      let deps := (j.getObjVal? "tech_stack" |>.toOption)
        |>.bind (fun ts => ts.getObjVal? "dependencies" |>.toOption)
        |>.bind (fun d => d.getArr? |>.toOption)
      match deps with
      | some arr =>
        return arr.toList.filterMap (fun d => (d.getStr? |>.toOption).map depBareName)
      | none => return []
  catch _ => return []

def isSrcFile (path : String) : Bool :=
  containsSub path "/src/" || containsSub path "\\src\\" || path.startsWith "src/"

def baseName (path : String) : String :=
  let unix := (path.splitOn "/").getLast!
  (unix.splitOn "\\").getLast!

def moduleOf (path : String) : String :=
  let b := baseName path
  (b.splitOn ".").head!

def scanFile (path : String) (ownModules deps : List String)
    (out : Array Finding) : IO (Array Finding) := do
  let text ← IO.FS.readFile path
  let isSrc := isSrcFile path
  let hard := if isSrc then "error" else "warning"
  let mut fs := out
  let mut lineNo := 0
  for (code, strl) in shadowLines text do
    lineNo := lineNo + 1
    let add (kind detail severity : String) : Finding :=
      { kind, file := path, line := lineNo, detail, severity }

    -- sorry/admit: an unproven hole is a failed validation anywhere.
    if hasToken code "sorry" || hasToken code "admit" then
      fs := fs.push (add "sorry" "unproven placeholder (`sorry`/`admit`)" "error")
    -- axiom: can prove anything; kernel won't flag it.
    if hasToken code "axiom" then
      fs := fs.push (add "axiom" "custom axiom declaration" hard)
    -- console output in library code
    if isSrc && (containsSub code "IO.println" || containsSub code "IO.print "
        || containsSub code "IO.eprintln" || containsSub code "IO.eprint "
        || hasToken code "dbg_trace") then
      fs := fs.push (add "print" "console output in library code" "error")
    -- blocking sleep
    if containsSub code "IO.sleep" then
      if isSrc then
        fs := fs.push (add "sleep" "IO.sleep blocks the caller" "error")
      else
        match numberAfter code "IO.sleep" with
        | some ms =>
          if ms > 1000 then
            fs := fs.push (add "sleep" s!"IO.sleep {ms}ms in tests - keep tests fast" "warning")
        | none => pure ()
    -- kernel-bypassing / totality-weakening constructs
    if hasToken code "native_decide" then
      fs := fs.push (add "native_decide" "native_decide trusts the compiler, not the kernel" "warning")
    if isSrc && hasToken code "unsafe" then
      fs := fs.push (add "unsafe" "unsafe definition escapes the type system" "warning")
    if isSrc && hasToken code "partial" then
      fs := fs.push (add "partial" "partial def skips the termination proof" "warning")
    -- env access
    if containsSub code "IO.getEnv" then
      fs := fs.push (add "env_var" "environment variable access" "warning")
    -- string-literal checks
    if containsSub strl "/home/" || containsSub strl "/Users/" || containsSub strl "/root/"
        || containsSub strl ":\\" then
      fs := fs.push (add "abs_path" "absolute path in literal" hard)
    if credentialNames.any (fun n => containsSub strl n) && containsSub strl ":="
        && containsSub strl "\"" then
      fs := fs.push (add "credential" "possible credential assignment" hard)
    if containsSub strl "http://" || containsSub strl "https://" then
      if !urlAllowed strl then
        fs := fs.push (add "hardcoded_url" "hardcoded URL" "warning")
    if hasHardcodedIp strl && !containsSub strl "127.0.0.1" then
      fs := fs.push (add "hardcoded_ip" "hardcoded IP address" hard)
    -- top-level numeric constant: def x := 42 / def x : Nat := 42
    if isSrc && code.startsWith "def " && containsSub code ":=" then
      match (strip ((code.splitOn ":=").getLast!)).toNat? with
      | some n => if n > 1 then
          fs := fs.push (add "hardcoded_value" "numeric constant - consider a parameter" "warning")
      | none => pure ()
    -- unlisted imports
    if isSrc || !isSrc then
      let t := strip code
      if t.startsWith "import " then
        let root := strip ((strip (dropStr t 7)).splitOn ".").head!
        -- Dep names are package names (lowercase, e.g. "mathlib"); import
        -- roots are module names (capitalized, e.g. "Mathlib") - match
        -- case-insensitively.
        if root.length > 0 && !stdlibRoots.contains root
            && !ownModules.contains root
            && !deps.any (fun d => d.toLower == root.toLower) then
          fs := fs.push (add "unlisted_import"
            s!"import {root} not in widget.json dependencies" "warning")
  return fs

def main (args : List String) : IO Unit := do
  let files := args.filter (fun a => a.endsWith ".lean" && baseName a != "lean_scanner.lean")
  let ownModules := files.map moduleOf
  let deps ← declaredDeps
  let mut findings : Array Finding := #[]
  for f in files do
    try
      findings ← scanFile f ownModules deps findings
    catch e =>
      findings := findings.push
        { kind := "scan_error", file := f, line := 0,
          detail := s!"could not read: {e}", severity := "error" }
  let out := Json.arr (findings.map Finding.toJson)
  -- single JSON line on stdout via the IO handle (self-clean: no IO.println)
  let stdout ← IO.getStdout
  stdout.putStrLn out.compress
