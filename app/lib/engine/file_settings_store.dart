import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import '../state/app_settings.dart';
import 'platform_paths.dart';

/// Settings as one JSON file under the user's config dir: the desktop
/// app's own, and the server's on behalf of browsers.
class FileSettingsStore implements SettingsStore {
  FileSettingsStore({File? file}) : file = file ?? defaultFile();
  final File file;

  static File defaultFile() => File('${mizpahPaths().config}${pathSep}app.json');

  @override
  Future<Map<String, dynamic>?> load() async {
    if (!file.existsSync()) return null;
    return jsonDecode(file.readAsStringSync()) as Map<String, dynamic>;
  }

  @override
  Future<void> save(Map<String, dynamic> settings) async {
    file.parent.createSync(recursive: true);
    file.writeAsStringSync(const JsonEncoder.withIndent(' ').convert(settings));
  }

  @override
  Future<String> storeSignatureImage(Uint8List bytes, String ext) async {
    final dest = File('${file.parent.path}${pathSep}signature.$ext');
    dest.parent.createSync(recursive: true);
    dest.writeAsBytesSync(bytes);
    return dest.path;
  }

  /// Until "task data vs. work" is settled, tasks default to the platform's
  /// per-user data directory: it moves with the OS's own backup rules.
  @override
  String get defaultRunsRoot => '${mizpahPaths().data}${pathSep}tasks';

  @override
  String get describe => file.path;
}
