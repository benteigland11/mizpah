/// A gym environment as saved under `<data>/bases/<name>`: a folder bound
/// read-only into every gym that names it, with the note the worker reads,
/// the env it gets, and what is installed.
class EnvironmentInfo {
  const EnvironmentInfo({
    required this.name,
    required this.path,
    this.note = '',
    this.env = const {},
    this.network = const [],
    this.isDefault = false,
    this.bytes = 0,
    this.files = 0,
    this.packages = const [],
    this.usedBy = const [],
    this.hasVenv = false,
    this.hasBin = false,
    this.madeAt,
  });

  final String name;

  /// The folder on the host; bound read-only at this same path in the sandbox.
  final String path;

  /// What the worker is told about the environment.
  final String note;

  /// Variables set for the worker, `$BASE` already the folder's path.
  final Map<String, String> env;

  /// Hosts a gym in this environment may reach beyond the default list.
  final List<String> network;

  /// The environment a gym gets when its brief names none.
  final bool isDefault;

  /// Size on disk and file count, links not followed.
  final int bytes;
  final int files;

  /// Python packages in its venv, `name version`.
  final List<String> packages;

  /// The tasks (ids) whose brief names this environment, or names none for
  /// the default.
  final List<String> usedBy;

  /// `venv/bin` goes first on PATH (and its site-packages on PYTHONPATH);
  /// `bin/` holds wrappers for downloaded programs, also on PATH.
  final bool hasVenv;
  final bool hasBin;
  final DateTime? madeAt;

  Map<String, dynamic> toJson() => {
    'name': name,
    'path': path,
    'note': note,
    'env': env,
    'network': network,
    'default': isDefault,
    'bytes': bytes,
    'files': files,
    'packages': packages,
    'used_by': usedBy,
    'venv': hasVenv,
    'bin': hasBin,
    if (madeAt != null) 'made_at': madeAt!.toUtc().toIso8601String(),
  };

  factory EnvironmentInfo.fromJson(Map<String, dynamic> j) => EnvironmentInfo(
    name: j['name'] as String? ?? '',
    path: j['path'] as String? ?? '',
    note: j['note'] as String? ?? '',
    env: {for (final e in ((j['env'] as Map?) ?? const {}).entries) '${e.key}': '${e.value}'},
    network: [for (final h in (j['network'] as List? ?? const [])) '$h'],
    isDefault: j['default'] == true,
    bytes: (j['bytes'] as num?)?.toInt() ?? 0,
    files: (j['files'] as num?)?.toInt() ?? 0,
    packages: [for (final p in (j['packages'] as List? ?? const [])) '$p'],
    usedBy: [for (final g in (j['used_by'] as List? ?? const [])) '$g'],
    hasVenv: j['venv'] == true,
    hasBin: j['bin'] == true,
    madeAt: j['made_at'] is String ? DateTime.tryParse(j['made_at'] as String) : null,
  );
}

/// One entry of an environment's file tree.
class FileNode {
  const FileNode({required this.name, required this.isDir, this.size, this.link, this.modified});
  final String name;
  final bool isDir;

  /// Bytes, for a file.
  final int? size;

  /// Where a symbolic link points, when this is one.
  final String? link;

  /// When it last changed (UTC).
  final DateTime? modified;

  Map<String, dynamic> toJson() =>
      {'name': name, 'dir': isDir, 'size': ?size, 'link': ?link, 'modified': ?modified?.toIso8601String()};

  factory FileNode.fromJson(Map<String, dynamic> j) => FileNode(
    name: j['name'] as String? ?? '',
    isDir: j['dir'] == true,
    size: (j['size'] as num?)?.toInt(),
    link: j['link'] as String?,
    modified: j['modified'] is String ? DateTime.tryParse(j['modified'] as String) : null,
  );
}

/// A file opened from an environment: its text when it is text and small
/// enough to edit, else only its size.
class EnvFile {
  const EnvFile({required this.size, this.text, this.binary = false, this.tooBig = false});
  final int size;

  /// Null when the file is binary or too big to edit here.
  final String? text;
  final bool binary;
  final bool tooBig;

  Map<String, dynamic> toJson() => {'size': size, 'text': ?text, 'binary': binary, 'too_big': tooBig};

  factory EnvFile.fromJson(Map<String, dynamic> j) => EnvFile(
    size: (j['size'] as num?)?.toInt() ?? 0,
    text: j['text'] as String?,
    binary: j['binary'] == true,
    tooBig: j['too_big'] == true,
  );
}
