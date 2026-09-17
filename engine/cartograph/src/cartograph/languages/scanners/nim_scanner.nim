## Cartograph Nim source scanner.
##
## Scans .nim files for contamination patterns with awareness of strings,
## comments, and multiline constructs. Outputs JSON for the Python engine.
##
## Usage: nim r nim_scanner.nim <file1.nim> [file2.nim ...]
## Output: JSON array of {file, kind, line, detail} objects.

import std/[json, os, strutils, sets]

type
  Finding = object
    file: string
    kind: string
    line: int
    detail: string
    severity: string  # "error" or "warning"

# Nim stdlib modules - skip for unlisted import checks.
# Generated from Nim 2.2.8 stdlib layout (lib/pure, lib/pure/collections,
# lib/std, lib/impure).
const nimStdlib = [
  # lib/pure/
  "std/algorithm", "std/async", "std/asyncdispatch", "std/asyncfile",
  "std/asyncfutures", "std/asynchttpserver", "std/asyncmacro",
  "std/asyncnet", "std/asyncstreams", "std/atomics", "std/base64",
  "std/bitops", "std/browsers", "std/cgi", "std/colors", "std/complex",
  "std/cookies", "std/coro", "std/cstrutils", "std/db_common",
  "std/db_mysql", "std/db_postgres", "std/db_sqlite", "std/distros",
  "std/dynlib", "std/encodings", "std/endians", "std/fenv",
  "std/hashes", "std/htmlgen", "std/htmlparser", "std/httpclient",
  "std/httpcore", "std/json", "std/lenientops", "std/lexbase",
  "std/logging", "std/macros", "std/marshal", "std/math", "std/md5",
  "std/memfiles", "std/mimetypes", "std/nativesockets", "std/net",
  "std/nimprof", "std/oids", "std/options", "std/os", "std/osproc",
  "std/parsecfg", "std/parsecsv", "std/parsejson", "std/parseopt",
  "std/parsesql", "std/parseutils", "std/parsexml", "std/pathnorm",
  "std/pegs", "std/posix", "std/prelude", "std/random", "std/rationals",
  "std/reservedmem", "std/ropes", "std/segfaults", "std/selectors",
  "std/ssl_certs", "std/ssl_config", "std/stats", "std/streams",
  "std/streamwrapper", "std/strformat", "std/strmisc", "std/strscans",
  "std/strtabs", "std/strutils", "std/sugar", "std/terminal", "std/times",
  "std/typetraits", "std/unicode", "std/unittest", "std/uri",
  "std/volatile", "std/xmlparser", "std/xmltree",
  # lib/pure/collections/
  "std/chains", "std/critbits", "std/deques", "std/heapqueue", "std/intsets",
  "std/lists", "std/sequtils", "std/sets", "std/sharedlist",
  "std/sharedtables", "std/tables",
  # lib/pure/concurrency/
  "std/cpuinfo", "std/cpuload", "std/threadpool",
  # lib/core/ (locks, typeinfo) — importable as std/<name>
  "std/locks", "std/rlocks", "std/typeinfo",
  # lib/std/
  "std/appdirs", "std/assertions", "std/cmdline", "std/compilesettings",
  "std/decls", "std/dirs", "std/editdistance", "std/effecttraits",
  "std/enumerate", "std/enumutils", "std/envvars", "std/exitprocs",
  "std/files", "std/formatfloat", "std/genasts", "std/importutils",
  "std/isolation", "std/jsbigints", "std/jsfetch", "std/jsformdata",
  "std/jsheaders", "std/jsonutils", "std/logic", "std/monotimes",
  "std/objectdollar", "std/oserrors", "std/outparams", "std/packedsets",
  "std/paths", "std/setutils", "std/sha1", "std/socketstreams",
  "std/stackframes", "std/staticos", "std/strbasics", "std/symlinks",
  "std/syncio", "std/sysatomics", "std/sysrand", "std/tasks",
  "std/tempfiles", "std/time_t", "std/typedthreads", "std/varints",
  "std/vmutils", "std/widestrs", "std/with", "std/wordwrap",
  "std/wrapnils",
  # lib/impure/
  "std/nre", "std/rdstdin", "std/re",
  # Short forms (without std/ prefix) - Nim allows both `import asyncdispatch`
  # and `import std/asyncdispatch`.
  "algorithm", "async", "asyncdispatch", "asyncfile", "asyncfutures",
  "asynchttpserver", "asyncmacro", "asyncnet", "asyncstreams", "atomics",
  "base64", "bitops", "browsers", "cgi", "chains", "colors", "complex",
  "cookies", "coro", "cpuinfo", "cpuload", "cstrutils", "critbits",
  "db_common", "db_mysql", "db_postgres",
  "db_sqlite", "deques", "distros", "dynlib", "encodings", "endians",
  "fenv", "hashes", "heapqueue", "htmlgen", "htmlparser", "httpclient",
  "httpcore", "intsets", "json", "lenientops", "lexbase", "lists",
  "logging", "macros", "marshal", "math", "md5", "memfiles", "mimetypes",
  "nativesockets", "net", "nimprof", "nre", "oids", "options", "os",
  "osproc", "parsecfg", "parsecsv", "parsejson", "parseopt", "parsesql",
  "parseutils", "parsexml", "pathnorm", "pegs", "posix", "prelude",
  "random", "rationals", "rdstdin", "re", "reservedmem", "ropes",
  "segfaults", "selectors", "sequtils", "sets", "sha1", "sharedlist",
  "sharedtables", "ssl_certs", "ssl_config", "stats", "streams",
  "streamwrapper", "strformat", "strmisc", "strscans", "strtabs",
  "strutils", "sugar", "tables", "terminal", "times", "typetraits", "unicode",
  "unittest", "uri", "volatile", "widestrs", "wordwrap", "wrapnils",
  "xmlparser", "xmltree",
  # lib/std/ short forms
  "appdirs", "assertions", "cmdline", "compilesettings", "decls", "dirs",
  "editdistance", "effecttraits", "enumerate", "enumutils", "envvars",
  "exitprocs", "files", "formatfloat", "genasts", "importutils",
  "isolation", "jsbigints", "jsfetch", "jsformdata", "jsheaders",
  "jsonutils", "logic", "monotimes", "objectdollar", "oserrors",
  "outparams", "packedsets", "paths", "setutils", "socketstreams",
  "stackframes", "staticos", "strbasics", "symlinks", "syncio",
  "sysatomics", "sysrand", "tasks", "tempfiles", "time_t",
  "typedthreads", "varints", "vmutils", "with",
  # Always available
  "system",
  # Legacy aliases
  "locks", "rlocks", "readline", "smtp", "sockets", "threadpool",
  "typeinfo", "winlean",
].toHashSet()

proc loadDeclaredDeps(): HashSet[string] =
  ## Read widget.json from cwd and extract dependency names.
  result = initHashSet[string]()
  try:
    let data = parseJson(readFile("widget.json"))
    let deps = data{"tech_stack", "dependencies"}
    if deps != nil and deps.kind == JArray:
      for dep in deps:
        if dep.kind == JString:
          # Strip version specifiers: "nimble_pkg>=1.0" -> "nimble_pkg"
          var bare = dep.getStr()
          for i, c in bare:
            if c in {' ', '>', '<', '=', '!', '~', ';', '['}:
              bare = bare[0 ..< i]
              break
          result.incl(bare.strip().toLower())
  except:
    discard

proc loadLocalModules(): HashSet[string] =
  ## Walk src/ for .nim files and return their bare module names (lowercased).
  ## A widget's tests/ and examples/ commonly import these as local modules
  ## (e.g. `import rotate_frame_lib` from a tests/ file). They are part of
  ## the widget itself, not external dependencies, so the unlisted-import
  ## check must allowlist them.
  result = initHashSet[string]()
  try:
    if dirExists("src"):
      for kind, path in walkDir("src"):
        if kind == pcFile and path.endsWith(".nim"):
          let bare = splitFile(path).name.toLower()
          if bare.len > 0:
            result.incl(bare)
  except:
    discard

let declaredDeps = loadDeclaredDeps()
let localModules = loadLocalModules()

proc isInCode(line: string): bool =
  ## Returns false if the line is a comment-only line.
  let stripped = line.strip()
  return stripped.len > 0 and not stripped.startsWith("#") and not stripped.startsWith("##")

# --- Import statement parsing -----------------------------------------------
#
# Nim has a rich import grammar that line-level regex can't handle reliably:
#   import foo                          # bare
#   import std/foo                      # slashed
#   import a, b, c                      # comma list
#   import std/[a, b, c]                # brace group
#   import std/[a, b], pkg/[c, d]       # multiple brace groups
#   import foo as bar                   # rename
#   import foo except a, b              # except clause
#   from foo import a, b                # from-import
#   import pkg/sub/mod                  # nested package path
#   import                              # multi-line continuation
#     std/hashes,
#     std/sets
#   import std/[                        # multi-line brace group
#     locks,
#     atomics
#   ]
#
# parseImportStatement takes a single logical import statement (already
# reassembled across lines) and returns the normalized module specs, each
# with the full dotted path and the root package name (for dep checking).

type ImportedModule = object
  fullPath: string   # e.g. "std/tables" or "chronos/apps/http"
  root: string       # first slash segment, e.g. "std" or "chronos"

proc rootOf(fullPath: string): string =
  let slash = fullPath.find('/')
  if slash < 0: fullPath else: fullPath[0 ..< slash]

proc splitTopLevelCommas(body: string): seq[string] =
  ## Split `body` on commas that are NOT inside a `[...]` brace group.
  result = @[]
  var depth = 0
  var cur = ""
  for c in body:
    if c == '[':
      depth += 1
      cur &= c
    elif c == ']':
      depth -= 1
      cur &= c
    elif c == ',' and depth == 0:
      let t = cur.strip()
      if t.len > 0: result.add(t)
      cur = ""
    else:
      cur &= c
  let t = cur.strip()
  if t.len > 0: result.add(t)

proc stripModifiers(spec: string): string =
  ## Drop ` as <alias>` and ` except <list>` suffixes from a single module
  ## spec. The alias/except symbols are NOT modules — they must not be
  ## checked against the stdlib or dep lists.
  var s = spec.strip()
  let asIdx = s.find(" as ")
  if asIdx >= 0:
    s = s[0 ..< asIdx].strip()
  let exIdx = s.find(" except ")
  if exIdx >= 0:
    s = s[0 ..< exIdx].strip()
  return s

proc parseImportStatement(stmt: string): seq[ImportedModule] =
  ## Parse a (possibly multi-line, already reassembled) import or from-import
  ## statement into its imported module specs.
  result = @[]
  var body: string
  let s = stmt.strip()
  if s.startsWith("from "):
    # `from <modspec> import <symbols...>` - only the modspec is a module
    let rest = s[5 .. ^1].strip()
    let importKw = rest.find(" import ")
    if importKw < 0:
      return
    body = rest[0 ..< importKw].strip()
  elif s.startsWith("import "):
    body = s[7 .. ^1].strip()
  else:
    return

  # `except` takes a comma-separated symbol list, not more module specs.
  # (Verified against `nim check`: `import std/os except putEnv, std/tables`
  # fails to compile.) Truncate at the first top-level ` except ` so its
  # symbols don't get mistaken for modules.
  let exceptIdx = body.find(" except ")
  if exceptIdx >= 0:
    body = body[0 ..< exceptIdx].strip()

  for rawSpec in splitTopLevelCommas(body):
    let spec = stripModifiers(rawSpec)
    if spec.len == 0:
      continue
    let obIdx = spec.find('[')
    if obIdx >= 0:
      let cbIdx = spec.find(']', obIdx)
      if cbIdx > obIdx:
        let prefix = spec[0 ..< obIdx].strip()
        let inner = spec[obIdx + 1 ..< cbIdx]
        for rawMember in inner.split(","):
          let m = rawMember.strip()
          if m.len == 0: continue
          let full = prefix & m
          result.add(ImportedModule(fullPath: full, root: rootOf(full)))
        continue
      # Unclosed brace group — malformed or truncated; skip this spec
      continue
    result.add(ImportedModule(fullPath: spec, root: rootOf(spec)))

proc isImportComplete(stmt: string): bool =
  ## Returns true if `stmt` is a complete import statement. An import
  ## is incomplete if it has an unclosed `[`, or ends with a trailing
  ## comma (meaning more modules are coming on the next line), or is
  ## just the bare `import` keyword awaiting its first module.
  let s = stmt.strip()
  if s == "import" or s == "from":
    return false
  var depth = 0
  for c in s:
    if c == '[': depth += 1
    elif c == ']': depth -= 1
  if depth != 0:
    return false
  if s.endsWith(","):
    return false
  return true

proc hasWordCall(code, name: string): bool =
  ## Returns true if `name(` appears in `code` with a non-identifier char
  ## immediately preceding it (or at the start of the string). Lets us
  ## flag `alloc(` while ignoring `realloc(` or `myalloc(`.
  var pos = 0
  while pos < code.len:
    let idx = code.find(name & "(", pos)
    if idx < 0:
      return false
    if idx == 0:
      return true
    let prev = code[idx - 1]
    if prev notin {'a'..'z', 'A'..'Z', '0'..'9', '_'}:
      return true
    pos = idx + 1
  return false

proc parenDelta(rawLine: string): int =
  ## Net change in paren/bracket depth across a single line, ignoring
  ## content inside string literals and after `#` comments. Used to
  ## carry signature-continuation state across lines so multi-line
  ## proc/func/template headers are not mistaken for top-level code.
  result = 0
  var i = 0
  let n = rawLine.len
  while i < n:
    let c = rawLine[i]
    if c == '#':
      break
    elif c == '"':
      # Triple-quote string: skip to closing triple-quote on same line if any
      if i + 2 < n and rawLine[i+1] == '"' and rawLine[i+2] == '"':
        i += 3
        while i + 2 < n and not (rawLine[i] == '"' and rawLine[i+1] == '"' and rawLine[i+2] == '"'):
          inc i
        if i + 2 < n: i += 3
        else: i = n
        continue
      # Regular string: skip to matching quote, honouring backslash escapes
      inc i
      while i < n and rawLine[i] != '"':
        if rawLine[i] == '\\' and i + 1 < n:
          i += 2
          continue
        inc i
      if i < n: inc i
      continue
    elif c == '\'':
      # Char literal 'x' or '\n' — skip to closing quote
      inc i
      if i < n and rawLine[i] == '\\' and i + 1 < n: i += 2
      elif i < n: inc i
      if i < n and rawLine[i] == '\'': inc i
      continue
    elif c == '(' or c == '[':
      inc result
    elif c == ')' or c == ']':
      dec result
    inc i

proc scanFile(filename: string): seq[Finding] =
  result = @[]
  let content = readFile(filename)
  let lines = content.splitLines()

  var inMultilineString = false
  var inRawString = false
  var topLevelSection = true
  # Paren/bracket depth carried across lines so multi-line proc/func/template
  # signatures (and other parenthesised continuations) are not misread as
  # separate top-level statements.
  var parenDepth = 0

  # Pending multi-line import accumulator. When we see an import statement
  # that isn't complete (trailing comma, unclosed brace, or bare `import`),
  # we buffer it and append subsequent indented continuation lines until it
  # parses cleanly. The recorded line number is the line where the import
  # started, so findings point at the right spot.
  var pendingImport = ""
  var pendingImportLine = 0

  for i, rawLine in lines:
    let lineNo = i + 1
    let stripped = rawLine.strip()
    let inTests = "/tests/" in filename or "\\tests\\" in filename
    let inExamples = "/examples/" in filename or "\\examples\\" in filename

    # Track multiline string literals (triple quotes)
    if inMultilineString:
      if "\"\"\"" in stripped:
        inMultilineString = false
      continue

    # Skip empty lines and pure comments
    if stripped.len == 0 or stripped.startsWith("#"):
      continue

    # Snapshot continuation state, then advance paren depth for this line.
    # `inContinuation` is true when this line is inside an unclosed paren/
    # bracket group from a previous line — typically a multi-line proc,
    # func, template, or macro signature, or a multi-line tuple/object
    # literal. Such lines are not standalone statements.
    let inContinuation = parenDepth > 0
    parenDepth += parenDelta(rawLine)
    if parenDepth < 0:
      parenDepth = 0

    # Check for multiline string start (but not end on same line)
    let tripleCount = stripped.count("\"\"\"")
    if tripleCount == 1:
      # Opens a multiline string, skip until close
      inMultilineString = true
      # Still check the code portion before the triple quote
      let beforeTriple = rawLine.split("\"\"\"")[0]
      if beforeTriple.strip().len == 0:
        continue

    # Strip inline comments (naive but handles common case)
    var code = stripped
    let hashPos = code.find(" #")
    if hashPos >= 0:
      # Make sure it's not inside a string
      var inStr = false
      var escaped = false
      for ci in 0 ..< hashPos:
        if escaped:
          escaped = false
          continue
        if code[ci] == '\\':
          escaped = true
          continue
        if code[ci] == '"':
          inStr = not inStr
      if not inStr:
        code = code[0 ..< hashPos].strip()

    if code.len == 0:
      continue

    let startsIndented = rawLine.len > 0 and rawLine[0].isSpaceAscii()
    # Don't flip topLevelSection while we're still inside a multi-line
    # signature: the closing `) =` or `): tuple[...]` of a proc header
    # is non-indented but is not the start of a new top-level statement.
    if not inContinuation and not startsIndented and
       not code.startsWith("import ") and not code.startsWith("from "):
      topLevelSection = true

    # --- Checks ---

    # echo detection - block in src/, warn in tests, allow in examples
    if code.startsWith("echo ") or code.startsWith("echo(") or code == "echo":
      if not inExamples:
        result.add(Finding(
          file: filename, kind: "echo", line: lineNo,
          detail: if inTests: "echo in test - consider removing debug output"
                  else: "echo call - remove debug output from src/",
          severity: if inTests: "warning" else: "error"))

    # quit detection
    if code.startsWith("quit") and (code.len == 4 or code[4] in {'(', ' '}):
      result.add(Finding(
        file: filename, kind: "quit", line: lineNo,
        detail: "quit() call - widgets must not exit the process",
        severity: "error"))

    if "system.quit" in code:
      result.add(Finding(
        file: filename, kind: "quit", line: lineNo,
        detail: "system.quit() call - widgets must not exit the process",
        severity: "error"))

    # C FFI pragmas - Cartograph-for-Nim is pure-Nim by policy.
    # For C bindings: publish a nimble package, depend on it via `dependencies`.
    if "{.importc" in code:
      result.add(Finding(
        file: filename, kind: "ffi", line: lineNo,
        detail: "{.importc.} is not permitted - Cartograph widgets are pure Nim. Use std/posix or similar stdlib wrappers, or publish C bindings as a nimble package and depend on it.",
        severity: "error"))

    if "{.compile" in code:
      result.add(Finding(
        file: filename, kind: "ffi", line: lineNo,
        detail: "{.compile.} is not permitted - Cartograph widgets are pure Nim. Shipping C source is out of scope; publish it as a nimble package instead.",
        severity: "error"))

    # Global mutable state
    if "{.global.}" in code:
      result.add(Finding(
        file: filename, kind: "global", line: lineNo,
        detail: "{.global.} - widgets must not use global mutable state",
        severity: "error"))

    # isMainModule guard
    if "when isMainModule" in code:
      result.add(Finding(
        file: filename, kind: "main_module", line: lineNo,
        detail: "when isMainModule - widgets are libraries, not executables",
        severity: "error"))

    # OS-specific when defined()
    const osTargets = ["windows", "linux", "macosx", "osx", "posix",
                       "unix", "freebsd", "netbsd", "openbsd", "haiku",
                       "android", "ios"]
    if "when defined(" in code.toLower():
      for target in osTargets:
        if ("defined(" & target) in code.toLower():
          result.add(Finding(
            file: filename, kind: "os_specific", line: lineNo,
            detail: "OS-specific when defined(" & target & ") - widgets must validate on all platforms",
            severity: "error"))
          break

    # cast[seq[T]] - GC-managed seq cast is a memory-safety hazard. Nim seqs
    # carry a GC header; casting between seq types (or between string and
    # seq[byte]) breaks GC invariants and can corrupt the heap. Use a copy
    # or `toOpenArrayByte` instead. Block in src/, warn in tests/examples.
    if "cast[seq[" in code:
      let isFatal = not inTests and not inExamples
      result.add(Finding(
        file: filename, kind: "cast_seq", line: lineNo,
        detail: "cast[seq[...]] - GC-managed seq cast is a memory-safety hazard; copy explicitly or use toOpenArrayByte",
        severity: if isFatal: "block" else: "warning"))

    # Bare `except:` swallows everything including Defect and KeyboardInterrupt.
    # Even with a non-empty handler, you almost always want a typed except
    # clause. `except CatchableError:` or `except IOError:` are fine.
    if code.startsWith("except:") or code == "except":
      let isDiscard = code == "except: discard" or
                      code.startsWith("except: discard ") or
                      code.startsWith("except:discard")
      result.add(Finding(
        file: filename, kind: "bare_except", line: lineNo,
        detail: if isDiscard:
                  "bare `except: discard` - silently swallows all errors including Defect; use `except CatchableError:` and handle or re-raise"
                else:
                  "bare `except:` - catches Defect and KeyboardInterrupt; use a typed except clause like `except CatchableError:`",
        severity: "warning"))

    # Raw memory primitives. Pure-Nim policy bans FFI, so the typical reason
    # for raw allocation is gone. Legitimate uses (zero-copy buffers, custom
    # allocators) still exist but are rare; warn so the author can justify
    # in checkin reason or wrap in a tested abstraction.
    block rawMemory:
      let memoryCalls = ["alloc", "alloc0", "allocShared", "allocShared0",
                         "createU", "createShared",
                         "dealloc", "deallocShared",
                         "realloc", "reallocShared",
                         "copyMem", "moveMem", "zeroMem"]
      var matched = ""
      for fn in memoryCalls:
        if hasWordCall(code, fn):
          matched = fn
          break
      if matched.len > 0:
        result.add(Finding(
          file: filename, kind: "raw_memory", line: lineNo,
          detail: matched & "() - raw memory primitive; widgets should prefer GC-managed seq/string or document why this is necessary",
          severity: "warning"))
      elif "cast[ptr " in code or "cast[ptr]" in code:
        result.add(Finding(
          file: filename, kind: "raw_memory", line: lineNo,
          detail: "cast[ptr ...] - raw pointer cast bypasses Nim's type system; document why this is necessary",
          severity: "warning"))
      elif "ptr UncheckedArray" in code:
        result.add(Finding(
          file: filename, kind: "raw_memory", line: lineNo,
          detail: "ptr UncheckedArray - raw memory access bypasses bounds checking; prefer seq/openArray or document why this is necessary",
          severity: "warning"))

    # Import checks via proper tokenizer (see parseImportStatement).
    # Multi-line imports are accumulated across iterations in pendingImport
    # and only parsed once isImportComplete returns true. This handles bare
    # `import`, trailing-comma continuations, and multi-line brace groups.
    const riskyModules = ["std/os", "std/osproc", "std/httpclient",
                          "std/net", "std/nativesockets",
                          "os", "osproc", "httpclient", "net", "nativesockets"]

    template processImport(stmt: string, startLine: int) =
      # Template (not proc) because it mutates `result` from the enclosing
      # scanFile body and Nim's closure rules forbid capturing var locals.
      let parsed = parseImportStatement(stmt)
      for mods in parsed:
        for rm in riskyModules:
          if mods.fullPath.toLower() == rm:
            result.add(Finding(
              file: filename, kind: "risky_import", line: startLine,
              detail: "import " & mods.fullPath & " - flagged for review",
              severity: "error"))
            break
      for mods in parsed:
        let full = mods.fullPath.toLower()
        let root = mods.root.toLower()
        # std_import_style: bare stdlib name without std/ prefix
        if "/" notin full and full in nimStdlib and full != "system":
          result.add(Finding(
            file: filename, kind: "std_import_style", line: startLine,
            detail: "import " & mods.fullPath & " - prefer std/" & mods.fullPath,
            severity: "warning"))
        # unlisted_import: check if any recognized form of the root package
        # is in stdlib, declared deps, or local src modules.
        let isStd = full in nimStdlib or ("std/" & full) in nimStdlib or
                    root == "std" or full.startsWith("std/")
        if isStd:
          continue
        if root in declaredDeps or full in declaredDeps:
          continue
        if root in localModules or full in localModules:
          continue
        # Relative imports (../src/foo, ./foo, src/foo) target the widget's
        # own modules, not external deps. Take the basename and re-check.
        if root == ".." or root == "." or root == "src":
          let basename = full.rsplit('/', 1)[^1]
          if basename in localModules:
            continue
        result.add(Finding(
          file: filename, kind: "unlisted_import", line: startLine,
          detail: "import " & mods.fullPath & " - not in widget.json dependencies",
          severity: if inTests or inExamples: "warning" else: "block"))

    if pendingImport.len > 0:
      # Continue accumulating a multi-line import regardless of indentation.
      # The loop terminates as soon as the accumulated text parses as a
      # complete import statement.
      pendingImport.add(" ")
      pendingImport.add(code)
      if isImportComplete(pendingImport):
        processImport(pendingImport, pendingImportLine)
        pendingImport = ""
      continue

    if code.startsWith("import ") or code.startsWith("from ") or
       code == "import" or code == "from":
      if isImportComplete(code):
        processImport(code, lineNo)
      else:
        pendingImport = code
        pendingImportLine = lineNo
      continue

    # Sleep/blocking: sleep(), sleepAsync()
    # In src/: block. In tests/examples: warn if duration > 1000.
    if code.startsWith("sleep") and (code.len == 5 or code[5] in {'(', ' '}):
      if not inTests and not inExamples:
        result.add(Finding(
          file: filename, kind: "sleep", line: lineNo,
          detail: "sleep() call - widgets must not block the caller",
          severity: "block"))
      else:
        # Check for large duration: sleep(2000)
        let parenPos = code.find("(")
        if parenPos >= 0:
          let afterParen = code[parenPos + 1 .. ^1].strip()
          let endPos = afterParen.find(")")
          if endPos > 0:
            let durStr = afterParen[0 ..< endPos].strip()
            try:
              let dur = parseInt(durStr)
              if dur > 1000:
                result.add(Finding(
                  file: filename, kind: "sleep", line: lineNo,
                  detail: "sleep(" & durStr & ") - consider reducing sleep duration",
                  severity: "warning"))
            except CatchableError:
              discard

    if "sleepAsync" in code:
      if not inTests and not inExamples:
        result.add(Finding(
          file: filename, kind: "sleep", line: lineNo,
          detail: "sleepAsync() call - widgets must not block the caller",
          severity: "block"))

    if "os.sleep" in code.toLower():
      if not inTests and not inExamples:
        result.add(Finding(
          file: filename, kind: "sleep", line: lineNo,
          detail: "os.sleep() call - widgets must not block the caller",
          severity: "block"))

    # --- Warning/block-level checks (contamination) ---

    # Absolute paths in strings (block)
    if "\"/" in rawLine or "\"C:" in rawLine or "\"c:" in rawLine:
      if rawLine.contains("\"/home/") or rawLine.contains("\"/Users/") or
         rawLine.contains("\"/root/") or rawLine.contains("\"C:") or
         rawLine.contains("\"c:"):
        result.add(Finding(
          file: filename, kind: "abs_path", line: lineNo,
          detail: "absolute path in string - widgets must be portable",
          severity: "block"))

    # Credentials (block in src, warning in tests)
    let lowerRaw = rawLine.toLower()
    if ("api_key" in lowerRaw or "secret_key" in lowerRaw or
        "access_token" in lowerRaw or "auth_token" in lowerRaw or
        "password" in lowerRaw or "credential" in lowerRaw) and
       "= \"" in rawLine and rawLine.count("\"") >= 2:
      result.add(Finding(
        file: filename, kind: "credential", line: lineNo,
        detail: if inTests: "possible credential in test - verify it's fake"
                else: "possible credential assignment",
        severity: if inTests: "warning" else: "block"))

    # Hardcoded URLs (warning, src/ only - tests and examples use mock URLs
    # as fixtures/demo data)
    if not inTests and not inExamples and
       ("\"http://" in rawLine or "\"https://" in rawLine):
      if not ("localhost" in rawLine or "127.0.0.1" in rawLine or
              "example.com" in rawLine or ".test/" in rawLine or
              ".test\"" in rawLine or ".test:" in rawLine):
        result.add(Finding(
          file: filename, kind: "hardcoded_url", line: lineNo,
          detail: "hardcoded URL in string",
          severity: "warning"))

    # Hardcoded IPs (block, src/ only) - tests and examples legitimately use
    # mock IPs as fixture data, matching the precedent for hardcoded_url and
    # hardcoded_value.
    if not inTests and not inExamples:
      block ipCheck:
        let qpos = rawLine.find('"')
        if qpos < 0: break ipCheck
        let after = rawLine[qpos + 1 .. ^1]
        var dots = 0
        var digits = 0
        var valid = false
        for ci in 0 ..< after.len:
          let c = after[ci]
          if c == '"':
            if dots == 3 and digits > 0:
              valid = true
            break
          elif c == '.':
            if digits == 0: break ipCheck
            dots += 1
            digits = 0
            if dots > 3: break ipCheck
          elif c.isDigit:
            digits += 1
            if digits > 3: break ipCheck
          elif c == ':' and dots == 3 and digits > 0:
            # port suffix like "10.0.0.1:8080" - keep scanning
            valid = true
            break
          else:
            break ipCheck
        if valid or (dots == 3 and digits > 0):
          result.add(Finding(
            file: filename, kind: "hardcoded_ip", line: lineNo,
            detail: "hardcoded IP address in string",
            severity: "block"))

    # Environment variable access
    if "getenv(" in code.toLower() or "getEnv(" in code or "envPairs" in code:
      result.add(Finding(
        file: filename, kind: "env_var", line: lineNo,
        detail: "environment variable access - verify it is not project-specific",
        severity: "warning"))

    # Hardcoded values: let/const with numeric literals (src/ only)
    # Tests legitimately have expected values, fixtures, and constants;
    # examples legitimately have demo constants (physics values, mock config).
    if not inTests and not inExamples and
       (code.startsWith("let ") or code.startsWith("const ")):
      let eqPos = code.find(" = ")
      if eqPos >= 0:
        let afterKeyword = code.split(" ", 1)[1].strip()
        let nameEnd = afterKeyword.find(" ")
        if nameEnd > 0:
          let varName = afterKeyword[0 ..< nameEnd].strip(chars = {':', '*'})
          let valPart = code[eqPos + 3 .. ^1].strip()
          if varName.len > 0 and valPart.len > 0:
            var checkVal = valPart
            if checkVal.startsWith("-"):
              checkVal = checkVal[1 .. ^1]
            if checkVal.len > 0 and (checkVal[0].isDigit or checkVal[0] == '.'):
              var allNumChars = true
              for c in checkVal:
                if c notin {'0'..'9', '.', '-', 'e', 'E', '+', '_', '\''}:
                  allNumChars = false
                  break
              if allNumChars:
                result.add(Finding(
                  file: filename, kind: "hardcoded_value", line: lineNo,
                  detail: varName & " = " & valPart & " - consider making this a parameter",
                  severity: "warning"))
            # String literal
            elif valPart.startsWith("\"") and valPart.endsWith("\"") and valPart.len > 2:
              let strVal = valPart[1 ..< valPart.len - 1]
              result.add(Finding(
                file: filename, kind: "hardcoded_value", line: lineNo,
                detail: varName & " = \"" & strVal[0 ..< min(strVal.len, 60)] & "\" - consider making this a parameter",
                severity: "warning"))

    # Top-level var (src/ only) - tests can have helper state at module scope;
    # examples are script-like and naturally declare top-level state.
    if not inTests and not inExamples and topLevelSection and
       code.startsWith("var ") and "{.global.}" notin code:
      let afterKeyword = code[4 .. ^1].strip()
      let splitters = [' ', ':', '=', '*']
      var endPos = afterKeyword.len
      for idx, ch in afterKeyword:
        if ch in splitters:
          endPos = idx
          break
      let varName = afterKeyword[0 ..< endPos].strip()
      if varName.len > 0:
        result.add(Finding(
          file: filename, kind: "top_level_var", line: lineNo,
          detail: "var " & varName & " - avoid top-level mutable state in widgets",
          severity: "warning"))

    if not startsIndented and (code.startsWith("proc ") or code.startsWith("func ") or
                               code.startsWith("template ") or code.startsWith("macro ") or
                               code.startsWith("iterator ") or code.startsWith("converter ") or
                               code.startsWith("method ") or code.startsWith("type ") or
                               code.startsWith("var ") or code.startsWith("let ") or
                               code.startsWith("const ")):
      topLevelSection = false


when isMainModule:
  if paramCount() < 1:
    echo """{"error": "usage: nim r nim_scanner.nim <file1.nim> [file2.nim ...]"}"""
    quit(1)

  var allFindings = newJArray()
  for i in 1 .. paramCount():
    let filename = paramStr(i)
    if not fileExists(filename):
      allFindings.add(%*{"file": filename, "kind": "error", "line": 0,
                         "detail": "file not found"})
      continue
    let findings = scanFile(filename)
    for f in findings:
      allFindings.add(%*{
        "file": f.file,
        "kind": f.kind,
        "line": f.line,
        "detail": f.detail,
        "severity": f.severity
      })

  echo $allFindings
