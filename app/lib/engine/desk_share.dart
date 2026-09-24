import 'dart:io';

import 'package:flutter/foundation.dart';

import 'engine.dart';
import 'provider_client.dart';
import 'rpc_server.dart';

/// Sharing this desk on the network: the engine and the web app served
/// from the running desktop app, so a browser on the tailnet (an iPad)
/// sees the same desk. Started and stopped from Settings.
class DeskShare extends ChangeNotifier {
  DeskShare({required this.engine, required this.providers, required this.fileRoots, this.webRoot});
  final Engine engine;
  final ProviderClient providers;
  final List<String> fileRoots;

  /// The built web app (`app/build/web`), if it exists.
  final Directory? webRoot;

  RpcServer? _server;
  bool get running => _server != null;
  int port = 0;
  String? token;
  String? error;

  /// Addresses the desk answers on, best first.
  List<String> addresses = const [];

  Future<void> start({required int port, String? token}) async {
    await stop();
    error = null;
    final s = RpcServer(
      engine: engine,
      providers: providers,
      webRoot: webRoot != null && webRoot!.existsSync() ? webRoot : null,
      token: (token ?? '').isEmpty ? null : token,
      fileRoots: fileRoots,
    );
    try {
      await s.start(port: port);
      _server = s;
      this.port = port;
      this.token = s.token;
      addresses = await _addresses();
    } catch (e) {
      error = '$e';
    }
    notifyListeners();
  }

  Future<void> stop() async {
    final s = _server;
    _server = null;
    if (s != null) await s.stop();
    notifyListeners();
  }

  /// A URL for a browser, for each address: Tailscale's 100.x first, then
  /// the hostname, then the rest.
  List<String> get urls => [
        for (final a in addresses) 'http://$a:$port/${token == null ? '' : '?token=$token'}',
      ];

  bool get hasWebApp => webRoot != null && webRoot!.existsSync();

  static Future<List<String>> _addresses() async {
    final out = <String>[];
    try {
      for (final i in await NetworkInterface.list(type: InternetAddressType.IPv4)) {
        for (final a in i.addresses) {
          if (a.isLoopback) continue;
          out.add(a.address);
        }
      }
    } catch (_) {}
    // Tailscale hands out 100.64/10: that is the one to type on the iPad.
    out.sort((a, b) => (a.startsWith('100.') ? 0 : 1).compareTo(b.startsWith('100.') ? 0 : 1));
    return [Platform.localHostname, ...out];
  }

  @override
  void dispose() {
    stop();
    super.dispose();
  }
}
