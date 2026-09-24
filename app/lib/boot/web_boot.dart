import 'dart:typed_data';

import 'package:web/web.dart' as web;

import '../engine/desk.dart';
import '../engine/remote_engine.dart';
import '../engine/rpc.dart';
import '../state/app_settings.dart';
import 'kinds.dart';

/// The engine across the wire: the page was served by the desk, so the
/// same origin answers RPC. `?token=` on the page URL, if any, rides along.
Future<Boot> boot({bool withHostWatch = false}) async {
  final loc = web.window.location;
  final token = Uri.parse(loc.href).queryParameters['token'];
  final rpc = RpcClient(loc.origin, token: token);
  final engine = RemoteEngine(rpc);
  final settings = AppSettings(_RemoteSettingsStore(engine));
  await settings.ready;
  try {
    Desk.home = (await engine.hello()).home;
  } catch (_) {
    // The banner will say the desk is away; paths show in full meanwhile.
  }
  // Settings changed on the desk (or another browser) come back with the
  // change stream.
  engine.changes.listen((_) => settings.reload());
  Desk.fileUrl = engine.fileUrl;
  return Boot(settings: settings, engine: engine, providers: RemoteProviderClient(rpc), remote: true, fileUrl: engine.fileUrl);
}

class _RemoteSettingsStore implements SettingsStore {
  _RemoteSettingsStore(this.engine);
  final RemoteEngine engine;

  @override
  Future<Map<String, dynamic>?> load() => engine.readAppSettings();
  @override
  Future<void> save(Map<String, dynamic> settings) => engine.writeAppSettings(settings);
  @override
  Future<String> storeSignatureImage(Uint8List bytes, String ext) => engine.storeSignatureImage(bytes, ext);
  @override
  String get defaultRunsRoot => '';
  @override
  String get describe => 'the desk at ${engine.rpc.base}';
}
