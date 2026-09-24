import 'dart:convert';
import 'dart:io';

import '../models/environment.dart';
import 'platform_paths.dart';
import 'state_dir.dart';

/// Where saved environments live: `<data>/bases`, as `mizpah.bases` keeps them.
Directory basesRoot() => Directory('${mizpahPaths().data}${pathSep}bases');

/// Every saved environment (a folder with a `base.json`), read straight off
/// disk the way `mizpah.bases` writes it. The default is `default.json`'s
/// name when that environment exists, else `bare`. [tasks] maps each task id
/// to the environment its brief names (null: none, so the default); without
/// it, the gyms root is scanned. Run in an isolate: the size walk visits
/// every file of a venv.
List<EnvironmentInfo> readEnvironments({Directory? root, Directory? gyms, Map<String, String?>? tasks}) {
  final bases = root ?? basesRoot();
  if (!bases.existsSync()) return const [];
  final folders = [
    for (final d in bases.listSync(followLinks: false))
      if (d is Directory && File('${d.path}${pathSep}base.json').existsSync()) d,
  ]..sort((a, b) => a.path.compareTo(b.path));
  final names = {for (final d in folders) _nameOf(d)};
  var fallback = 'bare';
  try {
    final named = (jsonDecode(File('${bases.path}${pathSep}default.json').readAsStringSync()) as Map)['name'];
    if (named is String && names.contains(named)) fallback = named;
  } catch (_) {}
  final users = tasks == null
      ? _gymsByEnvironment(gyms ?? Directory('${mizpahPaths().data}${pathSep}gyms'), fallback)
      : <String, List<String>>{};
  if (tasks != null) {
    for (final MapEntry(key: id, value: env) in tasks.entries) {
      users.putIfAbsent(env == null || env.trim().isEmpty ? fallback : env.trim(), () => []).add(id);
    }
  }
  return [
    for (final d in folders) _read(d, isDefault: _nameOf(d) == fallback, usedBy: users[_nameOf(d)] ?? const []),
  ];
}

String _nameOf(Directory d) => d.uri.pathSegments.where((s) => s.isNotEmpty).last;

EnvironmentInfo _read(Directory d, {required bool isDefault, required List<String> usedBy}) {
  Map<String, dynamic> record = const {};
  try {
    record = (jsonDecode(File('${d.path}${pathSep}base.json').readAsStringSync()) as Map).cast<String, dynamic>();
  } catch (_) {}
  var bytes = 0, files = 0;
  for (final e in d.listSync(recursive: true, followLinks: false)) {
    if (e is File) {
      files++;
      try {
        bytes += e.lengthSync();
      } catch (_) {}
    }
  }
  final packages = <String>[];
  final lib = Directory('${d.path}${pathSep}venv${pathSep}lib');
  if (lib.existsSync()) {
    for (final py in lib.listSync(followLinks: false)) {
      final site = Directory('${py.path}${pathSep}site-packages');
      if (!site.existsSync()) continue;
      for (final e in site.listSync(followLinks: false)) {
        final n = e.uri.pathSegments.where((s) => s.isNotEmpty).last;
        if (!n.endsWith('.dist-info')) continue;
        final stem = n.substring(0, n.length - '.dist-info'.length);
        final cut = stem.lastIndexOf('-');
        packages.add(cut < 0 ? stem : '${stem.substring(0, cut)} ${stem.substring(cut + 1)}');
      }
    }
  }
  packages.sort((a, b) => a.toLowerCase().compareTo(b.toLowerCase()));
  final env = <String, String>{
    for (final e in ((record['env'] as Map?) ?? const {}).entries) '${e.key}': '${e.value}'.replaceAll(r'$BASE', d.path),
  };
  return EnvironmentInfo(
    name: record['name'] as String? ?? _nameOf(d),
    path: d.path,
    note: record['note'] as String? ?? '',
    env: env,
    network: [for (final h in (record['network'] as List? ?? const [])) '$h'],
    isDefault: isDefault,
    bytes: bytes,
    files: files,
    packages: packages,
    usedBy: usedBy,
    hasVenv: Directory('${d.path}${pathSep}venv${pathSep}bin').existsSync(),
    hasBin: Directory('${d.path}${pathSep}bin').existsSync(),
    madeAt: File('${d.path}${pathSep}base.json').lastModifiedSync().toUtc(),
  );
}

/// Gym folder names per environment their brief names; a brief naming none
/// runs in the default.
Map<String, List<String>> _gymsByEnvironment(Directory gyms, String fallback) {
  final out = <String, List<String>>{};
  if (!gyms.existsSync()) return out;
  for (final g in gyms.listSync(followLinks: false)) {
    if (g is! Directory) continue;
    final brief = File('${stateDir(g.path)}${pathSep}brief.json');
    if (!brief.existsSync()) continue;
    var name = fallback;
    try {
      final env = (jsonDecode(brief.readAsStringSync()) as Map)['environment'];
      if (env is String && env.trim().isNotEmpty) name = env.trim();
    } catch (_) {}
    out.putIfAbsent(name, () => []).add(_nameOf(g));
  }
  for (final l in out.values) {
    l.sort();
  }
  return out;
}

/// One folder of an environment's tree: folders first, then files, by name.
/// [relative] is a path inside the environment; one that climbs out of it
/// lists nothing.
List<FileNode> listEnvironmentDir(String name, String relative, {Directory? root}) {
  final base = _inside(name, '', root: root);
  return base == null ? const [] : listDirUnder(base, relative);
}

/// One folder under [root] ([relative] inside it; '' is the top): folders
/// first, then files, by name, each with its size and when it last changed.
/// A path that climbs out of [root] lists nothing.
List<FileNode> listDirUnder(String root, String relative) {
  final path = insideRoot(root, relative);
  if (path == null) return const [];
  final target = Directory(path);
  if (!target.existsSync()) return const [];
  final out = <FileNode>[];
  for (final e in target.listSync(followLinks: false)) {
    final n = e.uri.pathSegments.where((s) => s.isNotEmpty).last;
    DateTime? modified;
    try {
      modified = e.statSync().modified.toUtc();
    } catch (_) {}
    if (e is Link) {
      String? to;
      try {
        to = e.targetSync();
      } catch (_) {}
      out.add(FileNode(name: n, isDir: FileSystemEntity.isDirectorySync(e.path), link: to, modified: modified));
    } else if (e is Directory) {
      out.add(FileNode(name: n, isDir: true, modified: modified));
    } else if (e is File) {
      int? size;
      try {
        size = e.lengthSync();
      } catch (_) {}
      out.add(FileNode(name: n, isDir: false, size: size, modified: modified));
    }
  }
  out.sort((a, b) => a.isDir != b.isDir ? (a.isDir ? -1 : 1) : a.name.toLowerCase().compareTo(b.name.toLowerCase()));
  return out;
}

/// The absolute path of [relative] under [root], or null when it would leave
/// [root].
String? insideRoot(String root, String relative) {
  final base = Uri.directory(Directory(root).absolute.path).normalizePath();
  final rel = relative.replaceAll('\\', '/').replaceFirst(RegExp(r'^/+'), '');
  final target = base.resolve(rel).normalizePath();
  final b = base.toFilePath(), t = target.toFilePath();
  if (t != b && !t.startsWith(b)) return null;
  return t.endsWith(pathSep) && t.length > 1 ? t.substring(0, t.length - 1) : t;
}

/// Largest file shown as a document or image (a PDF, an SVG, a picture).
const viewableBytes = 64 * 1024 * 1024;

/// A file's bytes under [root], for a viewer; refused past [viewableBytes].
List<int> readBytesUnder(String root, String relative) {
  final path = insideRoot(root, relative) ?? (throw ArgumentError('$relative is not inside $root'));
  final f = File(path);
  if (f.lengthSync() > viewableBytes) throw StateError('$relative is over ${viewableBytes ~/ (1024 * 1024)} MB');
  return f.readAsBytesSync();
}

/// A file under [root] for a read-only preview: its text when it is text and
/// at most [editableBytes], else only its size.
EnvFile readFileUnder(String root, String relative) {
  final path = insideRoot(root, relative) ?? (throw ArgumentError('$relative is not inside $root'));
  final f = File(path);
  final size = f.lengthSync();
  if (size > editableBytes) return EnvFile(size: size, tooBig: true);
  final bytes = f.readAsBytesSync();
  // A NUL in the first few KB: not text.
  if (bytes.take(8000).contains(0)) return EnvFile(size: size, binary: true);
  try {
    return EnvFile(size: size, text: utf8.decode(bytes));
  } on FormatException {
    return EnvFile(size: size, binary: true);
  }
}

// ---- editing an environment's files ---------------------------------------

/// Largest file the editor opens as text.
const editableBytes = 1024 * 1024;

/// A name `mizpah.bases` accepts: lowercase letters, digits, - and _.
final environmentName = RegExp(r'^[a-z0-9][a-z0-9_-]{0,39}$');

/// The absolute path of [relative] inside environment [name], or null when
/// it would leave the environment's folder (or the name is not one).
String? _inside(String name, String relative, {Directory? root}) {
  if (!environmentName.hasMatch(name)) return null;
  return insideRoot('${(root ?? basesRoot()).path}$pathSep$name', relative);
}

String _must(String name, String relative) =>
    _inside(name, relative) ?? (throw ArgumentError('$relative is not inside environment $name'));

EnvFile readEnvironmentFile(String name, String relative) => readFileUnder(_must(name, ''), relative);

/// Open an environment folder (and [relative] in it, when that is a file) in
/// the desk's code editor: `\$MIZPAH_EDITOR`, else VS Code, else the system's
/// opener. VS Code gets the folder as its workspace and the file shown (`-g`).
Future<void> openInEditor(String name, String relative) => openPathInEditor(_must(name, ''), _must(name, relative));

/// Open [root] as the editor's workspace, showing [target] when it is a file
/// inside it: `\$MIZPAH_EDITOR`, else VS Code, else the system's opener.
Future<void> openPathInEditor(String root, [String? target]) async {
  target ??= root;
  final isFile = FileSystemEntity.typeSync(target) == FileSystemEntityType.file;
  final chosen = Platform.environment['MIZPAH_EDITOR'];
  String? onPath(String exe) {
    for (final dir in (Platform.environment['PATH'] ?? '').split(Platform.isWindows ? ';' : ':')) {
      if (dir.isNotEmpty && File('$dir$pathSep$exe').existsSync()) return '$dir$pathSep$exe';
    }
    return null;
  }

  final code = chosen != null && chosen.trim().isNotEmpty ? chosen.trim() : onPath('code') ?? onPath('codium');
  final (exe, args) = code != null
      ? (code, [root, if (isFile) ...['-g', target]])
      : Platform.isMacOS
      ? ('open', [isFile ? target : root])
      : Platform.isWindows
      ? ('explorer', [isFile ? target : root])
      : ('xdg-open', [isFile ? target : root]);
  await Process.start(exe, args, mode: ProcessStartMode.detached);
}

/// A new, empty environment: its folder and a `base.json`, as
/// `mizpah.bases create` makes one.
void createEnvironment(String name, {String note = ''}) {
  if (!environmentName.hasMatch(name)) {
    throw ArgumentError('an environment name is lowercase letters, digits, - and _ (e.g. lean-4)');
  }
  final folder = Directory('${basesRoot().path}$pathSep$name');
  if (File('${folder.path}${pathSep}base.json').existsSync()) throw StateError('$name already exists');
  folder.createSync(recursive: true);
  File('${folder.path}${pathSep}base.json')
      .writeAsStringSync('${const JsonEncoder.withIndent(' ').convert({'name': name, 'note': note, 'env': {}})}\n');
}
