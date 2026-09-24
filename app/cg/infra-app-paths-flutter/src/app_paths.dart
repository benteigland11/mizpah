/// Per-user application directories, resolved the way each platform
/// expects: XDG on Linux, `AppData` on Windows, `~/Library` on macOS.
///
/// Pure: everything comes in as arguments (the environment map, the home
/// directory, the platform name), nothing is read from the process and no
/// directory is created. The rules match the Python widget of the same
/// name, so two programs written in different languages agree on where a
/// shared file lives.
class AppPaths {
  const AppPaths({
    required this.config,
    required this.state,
    required this.cache,
    required this.data,
  });

  /// Settings a person edits or the app writes on their behalf.
  final String config;

  /// Registries, journals: durable but regenerable.
  final String state;

  /// Safe to delete at any time.
  final String cache;

  /// The user's own content the app manages.
  final String data;

  @override
  String toString() =>
      'AppPaths(config: $config, state: $state, cache: $cache, data: $data)';

  @override
  bool operator ==(Object other) =>
      other is AppPaths &&
      other.config == config &&
      other.state == state &&
      other.cache == cache &&
      other.data == data;

  @override
  int get hashCode => Object.hash(config, state, cache, data);
}

/// Resolve [AppPaths] for [appName].
///
/// [environ] is the process environment as the caller sees it (pass
/// `Platform.environment` or a fake). [platform] is `linux`, `macos`,
/// `windows` or any spelling [normalizePlatform] accepts; [home] defaults
/// to `HOME` (or `USERPROFILE` on Windows) from [environ]. [vendorName]
/// nests the app under a vendor folder on Windows and macOS, as those
/// platforms prefer; Linux ignores it, as XDG does.
AppPaths resolveAppPaths(
  String appName, {
  required Map<String, String> environ,
  String vendorName = '',
  required String platform,
  String? home,
}) {
  final name = normalizeAppName(appName);
  final vendor = vendorName.isEmpty ? '' : normalizeAppName(vendorName);
  final os = normalizePlatform(platform);
  final sep = os == 'windows' ? r'\' : '/';
  String join(List<String> parts) => parts.where((p) => p.isNotEmpty).join(sep);
  final resolvedHome = home ??
      environ['HOME'] ??
      environ['USERPROFILE'] ??
      (throw ArgumentError('home is required when the environment has no HOME'));
  final relative = vendor.isEmpty ? name : join([vendor, name]);

  switch (os) {
    case 'windows':
      final roaming = environ['APPDATA'] ?? join([resolvedHome, 'AppData', 'Roaming']);
      final local = environ['LOCALAPPDATA'] ?? join([resolvedHome, 'AppData', 'Local']);
      return AppPaths(
        config: join([roaming, relative]),
        state: join([local, relative, 'State']),
        cache: join([local, relative, 'Cache']),
        data: join([local, relative, 'Data']),
      );
    case 'macos':
      final support = join([resolvedHome, 'Library', 'Application Support']);
      return AppPaths(
        config: join([support, relative]),
        state: join([support, relative]),
        cache: join([resolvedHome, 'Library', 'Caches', relative]),
        data: join([support, relative]),
      );
    default:
      return AppPaths(
        config: join([environ['XDG_CONFIG_HOME'] ?? join([resolvedHome, '.config']), name]),
        state: join([environ['XDG_STATE_HOME'] ?? join([resolvedHome, '.local', 'state']), name]),
        cache: join([environ['XDG_CACHE_HOME'] ?? join([resolvedHome, '.cache']), name]),
        data: join([environ['XDG_DATA_HOME'] ?? join([resolvedHome, '.local', 'share']), name]),
      );
  }
}

/// `windows`, `macos` or `linux` from any common spelling (`win32`,
/// `darwin`, `osx`, `cygwin`, …). Anything unrecognised is treated as Linux.
String normalizePlatform(String platform) {
  final raw = platform.trim().toLowerCase();
  if (raw.startsWith('win') || raw.startsWith('cygwin') || raw.startsWith('msys')) {
    return 'windows';
  }
  if (raw.startsWith('darwin') || raw == 'mac' || raw == 'macos' || raw == 'osx') {
    return 'macos';
  }
  return 'linux';
}

/// A folder-safe app name: letters, digits, `-`, `_`, `.` and spaces kept;
/// everything else dropped. Empty input is an error.
String normalizeAppName(String appName) {
  final trimmed = appName.trim();
  if (trimmed.isEmpty) throw ArgumentError('appName is required');
  final buf = StringBuffer();
  for (final rune in trimmed.runes) {
    final ch = String.fromCharCode(rune);
    final ok = RegExp(r'[A-Za-z0-9\-_. ]').hasMatch(ch);
    if (ok) buf.write(ch);
  }
  return buf.toString().trim();
}
