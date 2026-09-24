import 'dart:typed_data';

import 'package:flutter/material.dart';

/// Where settings are kept: a file on the desk, or the engine over the
/// wire when the app runs in a browser. One desk, one set of settings.
abstract class SettingsStore {
  /// The stored map, or null when there is nothing yet.
  Future<Map<String, dynamic>?> load();
  Future<void> save(Map<String, dynamic> settings);

  /// File an uploaded signature image; returns where it was put.
  Future<String> storeSignatureImage(Uint8List bytes, String ext);

  /// Where tasks go when nothing is set.
  String get defaultRunsRoot;
  String get describe;
}

/// What the app remembers between launches: appearance, where runs live,
/// how often to look, where the engine's tools are. One small JSON map,
/// wherever the [SettingsStore] keeps it. Read by MaterialApp (theme) and
/// the engine (paths, poll).
class AppSettings extends ChangeNotifier {
  AppSettings(this.store) : runsRoot = store.defaultRunsRoot {
    ready = _load();
  }

  final SettingsStore store;

  /// Resolves once the stored settings are in. Desktop stores answer at
  /// once; a remote one after a round trip.
  late final Future<void> ready;
  bool loaded = false;

  ThemeMode mode = ThemeMode.light;

  /// Start with the task list folded to its rail.
  bool sidebarCollapsed = false;

  /// The person who signs: printed name and title under the mark.
  String signerName = '';
  String signerTitle = defaultSignerTitle;
  static const defaultSignerTitle = 'Administrator';

  /// The mark itself, drawn once: strokes as lists of [x, y] in 0..1 of
  /// the pad. Empty means the name is set in a hand instead, unless an
  /// image was uploaded.
  List<List<List<double>>> signature = const [];

  /// An uploaded mark (PNG or JPEG), copied under the config dir so the
  /// original may move. Drawing a mark clears it; uploading clears strokes.
  String signatureImage = '';
  bool get hasSignatureImage => signatureImage.isNotEmpty;
  String runsRoot;
  int pollSeconds = 5;
  /// Where the engine is. Empty means find it: the sidecar beside the
  /// executable, or the repository when running from source.
  String engineRoot = '';

  /// The loop config's file name under `engine/mizpah`.
  String engineConfigName = 'config.openai.json';

  /// Set when a change needs a relaunch to take (engine tool paths).
  bool relaunchPending = false;

  /// Sharing this desk on the network (a browser on the tailnet). Off by
  /// default; the token is minted the first time it is turned on.
  bool shareEnabled = false;
  int sharePort = 7355;
  String shareToken = '';

  void setShare({bool? enabled, int? port, String? token}) {
    shareEnabled = enabled ?? shareEnabled;
    sharePort = port ?? sharePort;
    shareToken = token ?? shareToken;
    _save();
  }

  /// Models chosen lately, newest first, as `(provider, model)`: what a
  /// short picker offers before "see all". Capped; a re-pick moves to the
  /// front.
  List<(String, String)> recentModels = const [];
  static const recentModelsKept = 6;

  void noteModelUsed(String provider, String model) {
    final rest = recentModels.where((r) => r != (provider, model)).take(recentModelsKept - 1);
    recentModels = [(provider, model), ...rest];
    _save();
  }

  Future<void> _load() async {
    try {
      final j = await store.load();
      if (j == null) return;
      apply(j);
    } catch (e) {
      debugPrint('settings not read from ${store.describe}: $e');
    } finally {
      loaded = true;
      notifyListeners();
    }
  }

  /// Re-read from the store (another client changed them).
  Future<void> reload() => _load();

  /// Take every field from a stored map.
  void apply(Map<String, dynamic> j) {
    try {
      mode = switch (j['theme']) {
        'dark' => ThemeMode.dark,
        'system' => ThemeMode.system,
        _ => ThemeMode.light,
      };
      sidebarCollapsed = j['sidebar_collapsed'] == true;
      signerName = j['signer_name'] as String? ?? '';
      signerTitle = j['signer_title'] as String? ?? signerTitle;
      // The first build shipped with a different placeholder title.
      if (signerTitle == 'Head of Engineering') signerTitle = defaultSignerTitle;
      signatureImage = j['signature_image'] as String? ?? '';
      signature = [
        for (final stroke in (j['signature'] as List? ?? const []))
          [for (final pt in (stroke as List).cast<List<dynamic>>()) [(pt[0] as num).toDouble(), (pt[1] as num).toDouble()]],
      ];
      runsRoot = j['runs_root'] as String? ?? runsRoot;
      shareEnabled = j['share_enabled'] == true;
      sharePort = (j['share_port'] as num?)?.toInt() ?? sharePort;
      shareToken = j['share_token'] as String? ?? '';
      pollSeconds = (j['poll_seconds'] as num?)?.toInt() ?? pollSeconds;
      engineRoot = j['engine_root'] as String? ?? '';
      recentModels = [
        for (final r in (j['recent_models'] as List? ?? const []))
          if (r is Map && r['provider'] is String && r['model'] is String) (r['provider'] as String, r['model'] as String),
      ];
      // Earlier builds stored the config as an absolute path; keep the name.
      engineConfigName = (j['engine_config'] as String? ?? engineConfigName).split(RegExp(r'[/\\]')).last;
    } catch (_) {
      // A bad file is ignored, not fatal; the next save rewrites it.
    }
  }

  Map<String, dynamic> toMap() => {
          'theme': switch (mode) {
            ThemeMode.dark => 'dark',
            ThemeMode.system => 'system',
            ThemeMode.light => 'light',
          },
          'sidebar_collapsed': sidebarCollapsed,
          'signer_name': signerName,
          'signer_title': signerTitle,
          'signature': signature,
          if (signatureImage.isNotEmpty) 'signature_image': signatureImage,
          'runs_root': runsRoot,
          'share_enabled': shareEnabled,
          'share_port': sharePort,
          'share_token': shareToken,
          'poll_seconds': pollSeconds,
          if (engineRoot.isNotEmpty) 'engine_root': engineRoot,
          'engine_config': engineConfigName,
          'recent_models': [
            for (final (p, m) in recentModels) {'provider': p, 'model': m},
          ],
        };

  void _save() {
    // Settings that will not persist are worth a line in the log; the
    // app keeps running on what is in memory.
    store.save(toMap()).catchError((Object e) => debugPrint('settings not saved to ${store.describe}: $e'));
    notifyListeners();
  }

  void setMode(ThemeMode m) {
    mode = m;
    _save();
  }

  void setSigner({String? name, String? title}) {
    signerName = name ?? signerName;
    signerTitle = title ?? signerTitle;
    _save();
  }

  void setSignature(List<List<List<double>>> strokes) {
    signature = strokes;
    if (strokes.isNotEmpty) signatureImage = '';
    _save();
  }

  /// Keep a copy of an uploaded mark beside the settings and use it from there.
  Future<void> setSignatureImage(Uint8List bytes, String filename) async {
    final lower = filename.toLowerCase();
    final ext = lower.endsWith('.jpg') || lower.endsWith('.jpeg') ? 'jpg' : 'png';
    signatureImage = await store.storeSignatureImage(bytes, ext);
    signature = const [];
    _save();
  }

  void clearSignatureImage() {
    signatureImage = '';
    _save();
  }

  /// Signing needs someone to sign as: a name, or the title alone. A role
  /// signs; a name is a courtesy.
  bool get canSign => signerLine.isNotEmpty;

  /// The signature line as it goes onto a document: "Name, Title", or the
  /// title alone when no name is set.
  String get signerLine => [
    if (signerName.trim().isNotEmpty) signerName.trim(),
    if (signerTitle.trim().isNotEmpty) signerTitle.trim(),
  ].join(', ');

  /// The line as a signature block shows it, (name, title): title-only puts
  /// the title on the name line — the same split a stored line gets back.
  (String, String) get signerParts {
    final name = signerName.trim(), title = signerTitle.trim();
    return name.isNotEmpty ? (name, title) : (title, '');
  }

  /// What the hand font sets when there is no drawn or uploaded mark.
  String get markName => signerParts.$1;

  void setSidebarCollapsed(bool v) {
    sidebarCollapsed = v;
    _save();
  }

  void setRunsRoot(String p) {
    runsRoot = p.trim();
    _save();
  }

  void setPollSeconds(int s) {
    pollSeconds = s.clamp(1, 300);
    _save();
  }

  void setEngineRoot(String p) {
    engineRoot = p.trim();
    relaunchPending = true;
    _save();
  }

  void setEngineConfigName(String name) {
    engineConfigName = name.trim();
    relaunchPending = true;
    _save();
  }
}
