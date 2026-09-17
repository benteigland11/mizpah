# Cartograph GDScript contamination scanner (Godot 4).
#
# Runs headless via `godot --headless --path <widget> --script gdscript_scanner.gd
# -- <file.gd> ...`. The target file paths arrive on OS.get_cmdline_user_args()
# (the scanner's own path goes to --script, so it never scans itself). Emits a
# JSON array of findings on stdout:
#
#   [{"kind":"godot3_syntax","file":"src/x.gd","line":4,
#     "detail":"onready var -> @onready var","severity":"block"}, ...]
#
# A hand-written lexer blanks `#` line comments and "..."/'...'/triple-quoted
# strings to spaces before applying the code checks, so a keyword inside a
# string or comment never trips. Content checks (urls, ips, abs paths,
# credentials) run against the captured string literals instead.
#
# The headline check blocks deprecated Godot 3 syntax (the whole reason this
# engine exists - LLMs constantly emit Godot 3 code into Godot 4 projects).

extends SceneTree

var _sani: Array = []
var _cur: String = ""
var _line: int = 1
var _lits: Array = []


func _init() -> void:
	var findings: Array = []
	for path in OS.get_cmdline_user_args():
		_scan_file(path, findings)
	print(JSON.stringify(findings))
	quit(0)


func _emit(c: String) -> void:
	if c == "\n":
		_sani.append(_cur)
		_cur = ""
		_line += 1
	else:
		_cur += c


func _lex(src: String) -> void:
	_sani = []
	_cur = ""
	_line = 1
	_lits = []
	var i: int = 0
	var n: int = src.length()
	while i < n:
		var c: String = src[i]
		# line comment
		if c == "#":
			while i < n and src[i] != "\n":
				_emit(" ")
				i += 1
			continue
		# triple-quoted string
		if (c == '"' or c == "'") and i + 2 < n and src[i + 1] == c and src[i + 2] == c:
			var q: String = c
			var startln: int = _line
			var lit: String = ""
			_emit(" ")
			_emit(" ")
			_emit(" ")
			i += 3
			while i < n:
				if i + 2 < n and src[i] == q and src[i + 1] == q and src[i + 2] == q:
					_emit(" ")
					_emit(" ")
					_emit(" ")
					i += 3
					break
				var ch: String = src[i]
				lit += ch
				_emit("\n" if ch == "\n" else " ")
				i += 1
			_lits.append([startln, lit])
			continue
		# single-line string
		if c == '"' or c == "'":
			var q2: String = c
			var startln2: int = _line
			var lit2: String = ""
			_emit(" ")
			i += 1
			while i < n:
				var d: String = src[i]
				if d == "\\" and i + 1 < n:
					lit2 += d
					lit2 += src[i + 1]
					_emit(" ")
					_emit(" ")
					i += 2
					continue
				if d == q2:
					_emit(" ")
					i += 1
					break
				if d == "\n":
					break
				lit2 += d
				_emit(" ")
				i += 1
			_lits.append([startln2, lit2])
			continue
		_emit(c)
		i += 1
	_sani.append(_cur)


func _re(pattern: String) -> RegEx:
	var r: RegEx = RegEx.new()
	r.compile(pattern)
	return r


func _scan_file(path: String, findings: Array) -> void:
	var f: FileAccess = FileAccess.open(path, FileAccess.READ)
	if f == null:
		return
	var src: String = f.get_as_text()
	_lex(src)
	var p: String = path.replace("\\", "/")
	var is_src: bool = p.find("/src/") != -1 or p.begins_with("src/")
	var sev: String = "block" if is_src else "warning"

	# --- Godot 3 syntax: hard block everywhere (won't run on Godot 4) -------
	# (pattern, detail). Matched against the sanitized line so strings/comments
	# are immune.
	var g3: Array = [
		["(^|\\s)onready\\s+var\\b", "onready var -> @onready var"],
		["(^|\\s)export\\s*\\(", "export(Type) -> @export"],
		["^\\s*export\\s+var\\b", "export var -> @export var"],
		["^\\s*tool\\s*$", "tool -> @tool"],
		["\\byield\\s*\\(", "yield(...) -> await"],
		["\\bsetget\\b", "setget -> get:/set: accessors"],
		["\\.connect\\s*\\([^)]*\\bself\\b", "3-arg .connect(sig, self, \"m\") -> sig.connect(callable)"],
		["\\bPool(Byte|Int|Real|String|Vector2|Vector3|Color)Array\\b", "Pool*Array -> Packed*Array"],
		["\\b(KinematicBody2D|KinematicBody|Spatial|Directory|ARVRController|YSort|Reference)\\b", "renamed Godot 3 class -> use the Godot 4 name (CharacterBody*/Node3D/DirAccess/RefCounted/...)"],
		["\\.instance\\s*\\(\\s*\\)", ".instance() -> .instantiate()"],
		["\\brand_range\\s*\\(", "rand_range() -> randf_range()/randi_range()"],
		["\\.empty\\s*\\(\\s*\\)", ".empty() -> .is_empty()"],
	]
	var idx: int = 0
	for code in _sani:
		idx += 1
		for entry in g3:
			if _re(entry[0]).search(code) != null:
				_add(findings, "godot3_syntax", path, idx, entry[1], "block")

		if is_src:
			# console output in a library
			if _re("\\b(print|prints|printt|printraw|print_debug)\\s*\\(").search(code) != null:
				_add(findings, "print", path, idx, "print* in src/ - widgets are libraries", "block")
			# absolute node path coupling (bare $/... ; quoted forms caught as abs_path literal)
			if _re("\\$\\s*/").search(code) != null or _re("get_node\\s*\\(\\s*\\^?[\"']?/").search(code) != null:
				_add(findings, "abs_node_path", path, idx, "absolute node path - widgets must not assume scene structure", "block")
			# blocking sleeps
			if _re("OS\\.(delay_msec|delay_usec)\\s*\\(").search(code) != null:
				_add(findings, "sleep", path, idx, "OS.delay_* blocks the caller", "block")
			# env access
			if _re("OS\\.get_environment\\s*\\(").search(code) != null:
				_add(findings, "env_var", path, idx, "OS.get_environment in src/", "warning")
			# untyped var declaration (static typing nudge)
			if _re("^\\s*var\\s+[A-Za-z_][A-Za-z0-9_]*\\s*=").search(code) != null:
				_add(findings, "untyped_var", path, idx, "untyped var - add a type (var x: int = ...) or infer (:=)", "warning")
			# hardcoded numeric const tunable (!= 0/1)
			var m: RegExMatch = _re("^\\s*const\\s+[A-Za-z_][A-Za-z0-9_]*\\s*=\\s*(-?[0-9]+)\\b").search(code)
			if m != null:
				var num: String = m.get_string(1)
				if num != "0" and num != "1" and num != "-1":
					_add(findings, "hardcoded_value", path, idx, "hardcoded numeric const tunable in src/", "warning")
		# todo markers (any file)
		if _re("\\b(TODO|FIXME)\\b").search(code) != null:
			_add(findings, "todo", path, idx, "TODO/FIXME marker", "warning")

	# --- content checks over string literals --------------------------------
	for pair in _lits:
		var ln: int = pair[0]
		var lit: String = pair[1]
		var low: String = lit.to_lower()
		if (lit.find("http://") != -1 or lit.find("https://") != -1) and not _url_allowed(low):
			_add(findings, "hardcoded_url", path, ln, "URL string literal", "warning")
		if _looks_like_ipv4(lit):
			_add(findings, "hardcoded_ip", path, ln, "IP-like string literal", sev)
		# absolute filesystem / node path literal (res:// and user:// are fine)
		if lit.begins_with("/") and not lit.begins_with("//"):
			_add(findings, "abs_path", path, ln, "absolute path literal", sev)
		if lit.length() >= 3 and lit[1] == ":" and (lit[2] == "\\" or lit[2] == "/"):
			_add(findings, "abs_path", path, ln, "absolute path literal", sev)

	# credentials: a sanitized line naming a secret bound to a string literal
	var secret: RegEx = _re("(?i)(password|passwd|secret|api_?key|access_?key|auth_?token|private_?key)\\s*[:=]")
	var lit_lines: Dictionary = {}
	for pair2 in _lits:
		lit_lines[pair2[0]] = true
	var idx2: int = 0
	for code2 in _sani:
		idx2 += 1
		if lit_lines.has(idx2) and secret.search(code2) != null:
			_add(findings, "credential", path, idx2, "possible hardcoded credential", sev)


func _url_allowed(low: String) -> bool:
	return (
		low.find("localhost") != -1
		or low.find("127.0.0.1") != -1
		or low.find("example.com") != -1
		or low.find("example.org") != -1
		or low.find("example.net") != -1
		or low.find(".test") != -1
	)


func _looks_like_ipv4(s: String) -> bool:
	var r: RegEx = _re("\\b[0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3}\\b")
	return r.search(s) != null


func _add(findings: Array, kind: String, file: String, line: int, detail: String, severity: String) -> void:
	findings.append({
		"kind": kind,
		"file": file,
		"line": line,
		"detail": detail,
		"severity": severity,
	})
