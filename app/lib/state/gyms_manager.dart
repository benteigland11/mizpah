import 'package:flutter/foundation.dart';

import '../engine/engine.dart';
import '../models/environment.dart';

/// The Gyms page: every environment as a root in an explorer, browsed and
/// previewed here, edited in the desk's own code editor. A run freezes its
/// environment when it starts, so what is saved there reaches the next run.
class GymsManager extends ChangeNotifier {
  GymsManager(this._engine) {
    reload();
  }

  final Engine _engine;

  List<EnvironmentInfo> environments = const [];
  bool loading = true;

  /// The last thing that failed, in one line; cleared by the next success.
  String? problem;

  EnvironmentInfo? info(String env) => environments.where((e) => e.name == env).firstOrNull;

  /// Folders read so far, by `env:path` ('' is the environment's top).
  final Map<String, List<FileNode>> folders = {};
  final Set<String> unfolded = {};
  static String keyOf(String env, String path) => '$env:$path';

  /// What the right-hand pane shows: an environment ('' path: its overview),
  /// a folder, or a file with its preview.
  String? env;
  String path = '';
  bool isDir = true;
  EnvFile? preview;

  Future<void> reload() async {
    loading = true;
    notifyListeners();
    try {
      environments = await _engine.readEnvironmentDetails();
      problem = null;
    } catch (e) {
      problem = 'Could not read the environments: $e';
    }
    loading = false;
    if (env == null || info(env!) == null) {
      final first = environments.where((e) => e.isDefault).firstOrNull ?? environments.firstOrNull;
      if (first != null) {
        env = first.name;
        path = '';
        isDir = true;
        unfolded.add(keyOf(first.name, ''));
      }
    }
    // Files change in the editor, outside the app: every open folder is read again.
    for (final k in unfolded.toList()) {
      final i = k.indexOf(':');
      await _read(k.substring(0, i), k.substring(i + 1));
    }
    if (env != null && !isDir) await _preview();
    notifyListeners();
  }

  Future<void> _read(String env, String path) async {
    try {
      folders[keyOf(env, path)] = await _engine.listEnvironmentFiles(env, path);
    } catch (e) {
      problem = 'Could not read $env/$path: $e';
    }
  }

  /// Pick an entry: a folder (or an environment's root) unfolds or folds; a
  /// file opens its preview.
  Future<void> select(String env, String path, {required bool dir}) async {
    this.env = env;
    this.path = path;
    isDir = dir;
    preview = null;
    if (dir) {
      final k = keyOf(env, path);
      if (!unfolded.remove(k)) {
        unfolded.add(k);
        if (!folders.containsKey(k)) await _read(env, path);
      }
    } else {
      await _preview();
    }
    notifyListeners();
  }

  Future<void> _preview() async {
    final e = env;
    if (e == null) return;
    try {
      preview = await _engine.readEnvironmentFile(e, path);
    } catch (err) {
      problem = 'Could not read $path: $err';
    }
  }

  Future<void> openInEditor(String env, [String path = '']) async {
    try {
      await _engine.openEnvironmentInEditor(env, path);
      problem = null;
    } catch (e) {
      problem = 'Could not open the editor: $e';
    }
    notifyListeners();
  }

  /// A task's folder in the desk's code editor.
  Future<void> openTaskInEditor(String id) async {
    try {
      await _engine.openTaskInEditor(id);
      problem = null;
    } catch (e) {
      problem = 'Could not open the editor: $e';
    }
    notifyListeners();
  }

  Future<void> createEnvironment(String name) async {
    try {
      await _engine.createEnvironment(name);
      problem = null;
      env = name;
      path = '';
      isDir = true;
      unfolded.add(keyOf(name, ''));
      await reload();
    } catch (e) {
      problem = 'New environment failed: $e';
      notifyListeners();
    }
  }

  /// Make [name] the environment a gym gets when its brief names none.
  Future<void> makeDefault(String name) async {
    try {
      await _engine.setDefaultEnvironment(name);
    } catch (e) {
      problem = 'Could not set the default: $e';
    }
    await reload();
  }
}
