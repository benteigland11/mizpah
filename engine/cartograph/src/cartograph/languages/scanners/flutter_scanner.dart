// Cartograph contamination scanner for Flutter widgets.
//
// Stdlib-only, runs on the Dart SDK bundled with Flutter:
//     dart flutter_scanner.dart <file1.dart> <file2.dart> ...
//
// Outputs a JSON array of findings to stdout:
//     [{"kind": "...", "file": "...", "line": N, "detail": "...",
//       "severity": "block"|"warning"}]
//
// A hand-rolled lexer tracks // and (nested!) /* */ comments, single- and
// double-quoted strings, triple-quoted strings, and raw (r'...') strings,
// so banned tokens inside strings or comments never trip. String
// interpolation content is treated as string content. Two views are
// produced per line:
//   - noComments: comments blanked, string contents kept (for checks that
//     look INSIDE strings: paths, URLs, IPs, credentials, import URIs)
//   - codeOnly:   comments AND string contents blanked (for API-call
//     checks: print, exit, sleep, Platform.environment, top-level consts)
//
// Severity contract (mirrors the Go/Rust/Java scanners):
//   - print/exit: emitted for src/ only, severity block
//   - abs_path/credential/ip/sleep: block in src/, warning in tests/examples
//   - url/hardcoded_value/env_var/unlisted_import: warning
//   - sleep in tests/examples: warn only above ~1s (statically estimated)

import 'dart:convert';
import 'dart:io';

final RegExp absPath = RegExp(
    r'''(?:/home/|/Users/|/root/|(?<![\w.])[A-Za-z]:[/\\](?!/))[^\s"']{3,}''');
final RegExp credential = RegExp(
    r'''(?:api_?key|api_?secret|secret_?key|access_?token|auth_?token|password|passwd|credential)\s*=\s*["'][^"']{6,}["']''',
    caseSensitive: false);
final RegExp url = RegExp(r'''https?://[^\s"']{4,}''');
final RegExp urlAllowed = RegExp(
    r'''https?://(?:localhost|127\.0\.0\.1|(?:[\w-]+\.)*example\.(?:com|org|net)|[\w.-]+\.test)(?:[/:?#]|$)''');
final RegExp ip = RegExp(r'''\b(?:\d{1,3}\.){3}\d{1,3}\b''');
final RegExp printCall = RegExp(
    r'''(?:(?<![\w.$])(?:print|debugPrint)|\b(?:stdout|stderr)\s*\.\s*(?:write|writeln|writeAll|add))\s*\(''');
final RegExp exitCall = RegExp(r'''(?<![\w.$])exit\s*\(''');
final RegExp sleepCall = RegExp(r'''(?<![\w.$])sleep\s*\(''');
final RegExp sleepDuration = RegExp(
    r'''(?<![\w.$])sleep\s*\(\s*(?:const\s+)?Duration\s*\(\s*(\w+)\s*:\s*([\d_]+)''');
final RegExp envVar = RegExp(r'''Platform\s*\.\s*environment''');
final RegExp importLine = RegExp(r'''^\s*import\s+r?["']([^"']+)["']''');
final RegExp topLevelConst = RegExp(
    r'''^(?:const|final)\s+(?:int\s+|double\s+|num\s+)?[a-zA-Z_]\w*\s*=\s*(-?[\d_]+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*;''');
final RegExp pubspecName = RegExp(r'''^name:\s*([A-Za-z0-9_]+)''',
    multiLine: true);
final RegExp pubspecPathDep = RegExp(
    r'''^\s+([A-Za-z0-9_]+):\s*\{\s*path:''',
    multiLine: true);

// Packages that never need declaring: the SDKs the scaffold itself
// provides. dart:* imports are stdlib and skipped separately.
const Set<String> providedPackages = {'flutter', 'flutter_test'};

final List<Map<String, Object>> findings = [];

void main(List<String> args) {
  final declaredDeps = readDeclaredDeps();
  final ownPackage = readOwnPackage();
  for (final arg in args) {
    final base = arg.replaceAll('\\', '/').split('/').last;
    if (base == 'flutter_scanner.dart') {
      continue; // never scan ourselves
    }
    scanFile(arg, declaredDeps, ownPackage);
  }
  stdout.writeln(jsonEncode(findings));
}

void scanFile(String path, List<String> declaredDeps, String? ownPackage) {
  final norm = path.replaceAll('\\', '/');
  final isSrc = norm.contains('/src/') || norm.startsWith('src/');
  final String content;
  try {
    content = File(path).readAsStringSync();
  } on FileSystemException {
    return; // unreadable file - validation surfaces this elsewhere
  }

  final views = lex(content);
  final noComments = views[0];
  final codeOnly = views[1];

  for (var i = 0; i < noComments.length; i++) {
    final ln = i + 1;
    final withStrings = noComments[i];
    final code = codeOnly[i];

    // ---- string-content checks ----------------------------------------
    if (absPath.hasMatch(withStrings)) {
      emit('abs_path', path, ln, strip(withStrings),
          isSrc ? 'block' : 'warning');
    }
    if (credential.hasMatch(withStrings)) {
      emit('credential', path, ln, strip(withStrings),
          isSrc ? 'block' : 'warning');
    }
    final um = url.firstMatch(withStrings);
    if (um != null && !urlAllowed.hasMatch(um.group(0)!)) {
      emit('hardcoded_url', path, ln, strip(withStrings), 'warning');
    }
    final im = ip.firstMatch(withStrings);
    if (im != null && !code.contains(im.group(0)!)) {
      // in a string literal, not a numeric token in code
      emit('hardcoded_ip', path, ln, strip(withStrings),
          isSrc ? 'block' : 'warning');
    }

    // ---- code checks --------------------------------------------------
    if (isSrc) {
      if (printCall.hasMatch(code)) {
        emit('print', path, ln, strip(code), 'block');
      }
      if (exitCall.hasMatch(code)) {
        emit('exit', path, ln, strip(code), 'block');
      }
      if (envVar.hasMatch(code)) {
        emit('env_var', path, ln, strip(code), 'warning');
      }
      final hv = topLevelConst.firstMatch(code);
      if (hv != null) {
        final num = hv.group(1)!.replaceAll('_', '');
        if (num != '0' && num != '1' && num != '-1') {
          emit('hardcoded_value', path, ln, strip(code), 'warning');
        }
      }
    }
    if (sleepCall.hasMatch(code)) {
      if (isSrc) {
        emit('sleep', path, ln, strip(code), 'block');
      } else if (estimateSleepMillis(code) > 1000) {
        emit('sleep', path, ln, strip(code), 'warning');
      }
    }
    final imp = importLine.firstMatch(withStrings);
    if (imp != null && isSrc) {
      final uri = imp.group(1)!;
      if (!uri.startsWith('dart:') && uri.startsWith('package:')) {
        final pkg = uri.substring('package:'.length).split('/').first;
        if (!providedPackages.contains(pkg) &&
            pkg != ownPackage &&
            !declaredDeps.contains(pkg)) {
          emit('unlisted_import', path, ln, uri, 'warning');
        }
      }
    }
  }
}

/// Statically estimate a sleep duration in milliseconds. Non-literal or
/// unparseable arguments count as "long" so they surface for review.
int estimateSleepMillis(String code) {
  final m = sleepDuration.firstMatch(code);
  if (m == null) {
    return 1 << 62; // sleep(someVariable) - assume long
  }
  final unit = m.group(1)!;
  final value = int.tryParse(m.group(2)!.replaceAll('_', ''));
  if (value == null) {
    return 1 << 62;
  }
  switch (unit) {
    case 'microseconds':
      return value ~/ 1000;
    case 'milliseconds':
      return value;
    case 'seconds':
      return value * 1000;
    case 'minutes':
      return value * 60000;
    case 'hours':
      return value * 3600000;
    default:
      return 1 << 62;
  }
}

/// Read declared dependency names: tech_stack.dependencies bare names from
/// widget.json in CWD, plus pub path-dependency package names from
/// pubspec.yaml (blueprint-composed widgets are wired as path deps and are
/// declared in blueprint.json rather than widget.json).
List<String> readDeclaredDeps() {
  final deps = <String>[];
  final pubspec = File('pubspec.yaml');
  if (pubspec.existsSync()) {
    try {
      for (final m in pubspecPathDep.allMatches(pubspec.readAsStringSync())) {
        deps.add(m.group(1)!);
      }
    } on Object {
      // unreadable pubspec - path deps just stay undeclared
    }
  }
  final wj = File('widget.json');
  if (!wj.existsSync()) {
    return deps;
  }
  try {
    final decoded = jsonDecode(wj.readAsStringSync());
    final declared = decoded['tech_stack']?['dependencies'];
    if (declared is List) {
      for (final dep in declared) {
        if (dep is String) {
          deps.add(dep.split(RegExp(r'''[><=!~;\[]''')).first.trim());
        }
      }
    }
  } on Object {
    // unreadable widget.json - treat as no declared deps
  }
  return deps;
}

/// Read the widget's own package name from pubspec.yaml in CWD.
String? readOwnPackage() {
  final pubspec = File('pubspec.yaml');
  if (!pubspec.existsSync()) {
    return null;
  }
  try {
    return pubspecName.firstMatch(pubspec.readAsStringSync())?.group(1);
  } on Object {
    return null;
  }
}

// ---- lexer ----------------------------------------------------------------

/// Returns [noComments, codeOnly] line views. Comment characters are
/// replaced with spaces in both; string contents are kept in noComments
/// but replaced with spaces in codeOnly (quotes kept in both so patterns
/// like ="..." still anchor). Dart block comments nest; raw strings have
/// no escapes; interpolation is treated as string content.
List<List<String>> lex(String content) {
  final noC = StringBuffer();
  final codeO = StringBuffer();
  var i = 0;
  final n = content.length;

  const code = 0, lineComment = 1, blockComment = 2, str = 3, tripleStr = 4;
  var state = code;
  var blockDepth = 0;
  var quote = ''; // active quote char for str/tripleStr
  var raw = false; // active string is raw (no escapes)

  String at(int idx) => idx < n ? content[idx] : '';

  while (i < n) {
    final c = content[i];
    final c1 = at(i + 1);
    final c2 = at(i + 2);
    if (c == '\n') {
      if (state == lineComment) {
        state = code;
      }
      noC.write('\n');
      codeO.write('\n');
      i++;
      continue;
    }
    switch (state) {
      case code:
        if (c == '/' && c1 == '/') {
          state = lineComment;
          noC.write('  ');
          codeO.write('  ');
          i += 2;
        } else if (c == '/' && c1 == '*') {
          state = blockComment;
          blockDepth = 1;
          noC.write('  ');
          codeO.write('  ');
          i += 2;
        } else if ((c == 'r' && (c1 == "'" || c1 == '"')) ||
            c == "'" ||
            c == '"') {
          raw = c == 'r';
          final q = raw ? c1 : c;
          final qi = raw ? i + 1 : i; // index of the first quote char
          if (raw) {
            noC.write('r');
            codeO.write('r');
          }
          if (at(qi + 1) == q && at(qi + 2) == q) {
            state = tripleStr;
            noC.write(q * 3);
            codeO.write(q * 3);
            i = qi + 3;
          } else {
            state = str;
            noC.write(q);
            codeO.write(q);
            i = qi + 1;
          }
          quote = q;
        } else {
          noC.write(c);
          codeO.write(c);
          i++;
        }
      case lineComment:
        noC.write(' ');
        codeO.write(' ');
        i++;
      case blockComment:
        if (c == '/' && c1 == '*') {
          blockDepth++;
          noC.write('  ');
          codeO.write('  ');
          i += 2;
        } else if (c == '*' && c1 == '/') {
          blockDepth--;
          if (blockDepth == 0) {
            state = code;
          }
          noC.write('  ');
          codeO.write('  ');
          i += 2;
        } else {
          noC.write(' ');
          codeO.write(' ');
          i++;
        }
      case str:
        if (!raw && c == r'\') {
          noC.write(c);
          noC.write(c1);
          codeO.write('  ');
          i += 2;
        } else if (c == quote) {
          state = code;
          noC.write(quote);
          codeO.write(quote);
          i++;
        } else {
          noC.write(c);
          codeO.write(' ');
          i++;
        }
      case tripleStr:
        if (!raw && c == r'\') {
          noC.write(c);
          noC.write(c1);
          codeO.write('  ');
          i += 2;
        } else if (c == quote && c1 == quote && c2 == quote) {
          state = code;
          noC.write(quote * 3);
          codeO.write(quote * 3);
          i += 3;
        } else {
          noC.write(c);
          codeO.write(' ');
          i++;
        }
    }
  }
  return [
    noC.toString().split('\n'),
    codeO.toString().split('\n'),
  ];
}

// ---- output ---------------------------------------------------------------

String strip(String s) {
  final t = s.trim();
  return t.length > 160 ? t.substring(0, 160) : t;
}

void emit(String kind, String file, int line, String detail, String severity) {
  findings.add({
    'kind': kind,
    'file': file,
    'line': line,
    'detail': detail,
    'severity': severity,
  });
}
