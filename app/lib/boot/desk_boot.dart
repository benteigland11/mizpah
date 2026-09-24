import 'dart:io';

import '../engine/desk.dart';
import '../engine/desk_share.dart';
import '../engine/platform_paths.dart';
import '../engine/engine_home.dart';
import '../engine/local_engine.dart';
import '../engine/file_settings_store.dart';
import '../engine/host_watch.dart';
import '../engine/provider_client.dart';
import '../state/app_settings.dart';
import '../widgets/signature.dart';
import 'kinds.dart';

/// The engine in this process: the desk has the files, the tools and the
/// loops. Used by the desktop app and, headless, by the server.
Future<Boot> boot({bool withHostWatch = true}) async {
  final settings = AppSettings(FileSettingsStore());
  await settings.ready;
  Desk.home = Platform.environment['HOME'] ?? Platform.environment['USERPROFILE'] ?? '';
  final home = EngineHome.locate(override: settings.engineRoot) ??
      EngineHome('${File(Platform.resolvedExecutable).parent.path}${Platform.pathSeparator}engine');
  final engine = LocalEngine(runsRoot: Directory(settings.runsRoot))
    ..engine = home
    ..engineConfig = home.config(settings.engineConfigName);
  // The tasks folder follows the setting live; engine tool paths are read
  // once, at launch.
  var root = settings.runsRoot;
  void wire() {
    engine.signer = settings.signerLine;
    if (settings.runsRoot != root) {
      root = settings.runsRoot;
      engine.setRunsRoot(Directory(root));
    }
  }

  wire();
  engine.signatureMark = () => settings.hasSignatureImage ? File(settings.signatureImage).readAsBytes() : strokesToPng(settings.signature);
  settings.addListener(wire);
  // A browser client writing settings through this engine: pick them up.
  engine.settingsChanged = () => settings.reload();

  HostWatch? host;
  if (withHostWatch) {
    // The app catches what a loop cannot: processes left on the host after
    // the loop that started them is gone.
    host = HostWatch(runsRoot: Directory(settings.runsRoot), poll: Duration(seconds: settings.pollSeconds));
    settings.addListener(() {
      host!.setRunsRoot(Directory(settings.runsRoot));
      host.setPoll(Duration(seconds: settings.pollSeconds));
    });
  }
  final providers = ProcessProviderClient(executable: home.provider, config: home.config(settings.engineConfigName));
  // Sharing follows the setting: on at launch if it was on, off when turned off.
  final share = DeskShare(
    engine: engine,
    providers: providers,
    fileRoots: [mizpahPaths().config],
    webRoot: Directory('${home.root}${Platform.pathSeparator}app${Platform.pathSeparator}build${Platform.pathSeparator}web'),
  );
  var sharing = (enabled: false, port: 0, token: '');
  Future<void> follow() async {
    final want = (enabled: settings.shareEnabled, port: settings.sharePort, token: settings.shareToken);
    if (want == sharing) return;
    sharing = want;
    if (want.enabled) {
      await share.start(port: want.port, token: want.token);
    } else {
      await share.stop();
    }
  }

  await follow();
  settings.addListener(follow);
  return Boot(settings: settings, engine: engine, providers: providers, host: host, share: share);
}
