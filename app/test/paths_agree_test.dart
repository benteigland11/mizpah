import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mizpah_app/cg/app_paths/app_paths.dart';

/// The engine (Python) and the app (Dart) must resolve the same per-user
/// directories from the same environment, or the registry the engine
/// writes is not the one the app reads. Pins the two widgets to each
/// other for every platform, through the engine's own interpreter.
void main() {
  final python = File('../.venv/bin/python');
  final engine = Directory('../engine/mizpah');

  test('infra-app-paths: python and flutter agree', () async {
    if (!python.existsSync() || !engine.existsSync()) {
      markTestSkipped('workspace venv not present');
      return;
    }
    const cases = [
      ('linux', {'HOME': '/home/u', 'XDG_STATE_HOME': '/var/s'}),
      ('linux', {'HOME': '/home/u'}),
      ('darwin', {'HOME': '/Users/u'}),
      ('win32', {'USERPROFILE': r'C:\Users\u', 'APPDATA': r'C:\Users\u\AppData\Roaming', 'LOCALAPPDATA': r'C:\Users\u\AppData\Local'}),
    ];
    for (final (platform, env) in cases) {
      final home = env['HOME'] ?? env['USERPROFILE']!;
      final script = '''
import json, sys
sys.path.insert(0, "${engine.absolute.path}")
from cg.infra_app_paths_python.src.app_paths import resolve_app_paths
p = resolve_app_paths("mizpah", vendor_name="", platform="$platform", home=r"$home", environ=${_pyDict(env)})
print(json.dumps(dict(config=str(p.config_dir), state=str(p.state_dir), cache=str(p.cache_dir), data=str(p.data_dir))))
''';
      final r = await Process.run(python.path, ['-c', script]);
      expect(r.exitCode, 0, reason: r.stderr.toString());
      final py = (r.stdout as String).trim();
      final dart = resolveAppPaths('mizpah', environ: env, platform: platform, home: home);
      // Python's Path renders the host's separator; on Linux the Windows
      // case comes back with '/' so compare separator-insensitively.
      String norm(String s) => s.replaceAll(r'\', '/');
      final pyMap = (jsonDecode(py) as Map).cast<String, String>();
      expect(norm(dart.config), norm(pyMap['config']!), reason: '$platform config');
      expect(norm(dart.state), norm(pyMap['state']!), reason: '$platform state');
      expect(norm(dart.cache), norm(pyMap['cache']!), reason: '$platform cache');
      expect(norm(dart.data), norm(pyMap['data']!), reason: '$platform data');
    }
  });
}

String _pyDict(Map<String, String> env) =>
    '{${env.entries.map((e) => '"${e.key}": r"${e.value}"').join(', ')}}';
