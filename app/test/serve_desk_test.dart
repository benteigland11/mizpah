// Not a test: a way to serve the desk from a terminal while the desktop
// app cannot (an older build is running, or none). Skipped unless asked:
//
//   MIZPAH_SERVE=1 MIZPAH_TOKEN=<token> flutter test test/serve_desk_test.dart
//
// It goes through the test runner only because the engine layer links
// Flutter (dart:ui), which a plain `dart run` cannot load.
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mizpah_app/boot/desk_boot.dart';
import 'package:mizpah_app/engine/platform_paths.dart';
import 'package:mizpah_app/engine/rpc_server.dart';

void main() {
  final env = Platform.environment;
  test(
    'serve the desk',
    () async {
      final b = await boot(withHostWatch: false);
      final token = env['MIZPAH_TOKEN'] ?? b.settings.shareToken;
      final port = int.tryParse(env['MIZPAH_PORT'] ?? '') ?? b.settings.sharePort;
      final s = RpcServer(
        engine: b.engine,
        providers: b.providers,
        webRoot: Directory('build/web'),
        token: token.isEmpty ? null : token,
        fileRoots: [mizpahPaths().config],
      );
      await s.start(port: port);
      stdout.writeln('SERVING on port $port${token.isEmpty ? '' : ' token $token'}');
      await ProcessSignal.sigint.watch().first;
      await s.stop();
    },
    skip: env['MIZPAH_SERVE'] != '1',
    timeout: Timeout.none,
  );
}
