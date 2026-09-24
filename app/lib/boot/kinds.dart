import '../engine/desk_share.dart';
import '../engine/engine.dart';
import '../engine/host_watch.dart';
import '../engine/provider_client.dart';
import '../state/app_settings.dart';

/// Everything the shell needs from wherever the engine is: in this
/// process on the desk, or across the wire in a browser.
class Boot {
  const Boot({
    required this.settings,
    required this.engine,
    required this.providers,
    this.host,
    this.remote = false,
    this.fileUrl,
    this.share,
  });
  final AppSettings settings;
  final Engine engine;
  final ProviderClient providers;

  /// Processes left behind on the host; only the desk itself can look.
  final HostWatch? host;

  /// True in a browser: some doors are closed (no folder picker, no app close).
  final bool remote;

  /// How to load a file the desk holds (a signature image) where `File`
  /// will not do; null on the desk.
  final String Function(String path)? fileUrl;

  /// Sharing this desk on the network; only the desk can.
  final DeskShare? share;
}
