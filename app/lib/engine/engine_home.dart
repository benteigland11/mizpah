import 'dart:io';

/// Where the engine lives: one root with the Python environment and the
/// `engine/` tree under it. In development that is the repository; in a
/// packaged app it is the sidecar folder shipped beside the executable.
/// Every tool path the app runs is derived from here, never spelled out.
class EngineHome {
  const EngineHome(this.root);
  final String root;

  static String get _sep => Platform.isWindows ? r'\' : '/';
  static String get _exe => Platform.isWindows ? '.exe' : '';

  String get venv => '$root$_sep.venv';
  String get bin => Platform.isWindows ? '$venv${_sep}Scripts' : '$venv${_sep}bin';
  String get python => '$bin${_sep}python$_exe';
  String tool(String name) => '$bin$_sep$name$_exe';
  String get terra => tool('terra');
  String get playbook => tool('playbook');
  String get provider => tool('mizpah-provider');

  /// `engine/mizpah`: the loop package, run with `python -m mizpah.<module>`
  /// from this directory.
  String get mizpahDir => '$root${_sep}engine${_sep}mizpah';
  String config(String name) => '$mizpahDir$_sep$name';

  /// Whether the tools are actually there.
  bool get present => File(terra).existsSync() && Directory(mizpahDir).existsSync();

  static bool _looksLikeRoot(String dir) => EngineHome(dir).present;

  /// Find the engine, in order: an explicit [override] (a setting), the
  /// `MIZPAH_ENGINE` environment variable, a sidecar `engine` folder
  /// beside the executable, then the nearest ancestor of the executable
  /// or the working directory that holds `.venv` and `engine/` (the
  /// repository, when running from `flutter run`). Null when nothing
  /// qualifies; the caller decides what to show.
  static EngineHome? locate({String? override, Map<String, String>? environ, String? executable, String? cwd}) {
    final env = environ ?? Platform.environment;
    final candidates = <String>[
      if (override != null && override.trim().isNotEmpty) override.trim(),
      if ((env['MIZPAH_ENGINE'] ?? '').isNotEmpty) env['MIZPAH_ENGINE']!,
    ];
    final exe = executable ?? Platform.resolvedExecutable;
    final exeDir = File(exe).parent.path;
    candidates.add('$exeDir${_sep}engine');
    for (final start in [exeDir, cwd ?? Directory.current.path]) {
      var d = Directory(start);
      while (true) {
        candidates.add(d.path);
        final up = d.parent;
        if (up.path == d.path) break;
        d = up;
      }
    }
    for (final c in candidates) {
      if (_looksLikeRoot(c)) return EngineHome(c);
    }
    return null;
  }
}
