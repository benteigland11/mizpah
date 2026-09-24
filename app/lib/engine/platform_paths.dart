import 'dart:io';

import '../cg/app_paths/app_paths.dart';

/// Where this app keeps its files on the machine it is running on, from
/// the process environment. One call site, so every default agrees with
/// the engine (which resolves the same rules in Python).
AppPaths mizpahPaths() => resolveAppPaths(
  'mizpah',
  environ: Platform.environment,
  platform: Platform.operatingSystem,
);

/// The platform's path separator, for joining onto a resolved directory.
String get pathSep => Platform.isWindows ? r'\' : '/';
