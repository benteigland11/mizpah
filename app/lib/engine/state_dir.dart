import 'dart:io';

/// Where a project keeps Mizpah's state: `.mizpah/` in the shipped layout
/// (brief, route, map, sessions), or `.terra/` for a project made by the
/// earlier engine. Mirrors the engine's `layout.dirname`.
String stateDirName(String project) {
  if (!Directory('$project/.mizpah').existsSync() && Directory('$project/.terra').existsSync()) {
    return '.terra';
  }
  return '.mizpah';
}

String stateDir(String project) => '$project/${stateDirName(project)}';

/// A directory is a project when it holds either tree.
bool isProject(String path) =>
    Directory('$path/.mizpah').existsSync() || Directory('$path/.terra').existsSync();

/// The map the current brief works on: `map/sessions/b_<slug>` for a
/// `.mizpah` project, `map` (global) for a legacy one. Mirrors the
/// engine's `layout.map_root`; the slug rule is the engine's.
String mapRoot(String project) {
  final state = stateDir(project);
  if (stateDirName(project) == '.terra') return '$state/map';
  try {
    final text = File('$state/brief.json').readAsStringSync();
    final title = RegExp(r'"title"\s*:\s*"([^"]*)"').firstMatch(text)?.group(1) ?? 'brief';
    var slug = title.toLowerCase().replaceAll(RegExp(r'[^a-z0-9]+'), '_').replaceAll(RegExp(r'^_+|_+$'), '');
    if (slug.length > 40) slug = slug.substring(0, 40);
    if (slug.isEmpty) slug = 'brief';
    final candidate = '$state/map/sessions/b_$slug';
    return Directory(candidate).existsSync() ? candidate : '$state/map';
  } catch (_) {
    return '$state/map';
  }
}
