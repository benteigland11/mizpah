/// Look inside an archive without unpacking it to disk: its entries as a
/// folder tree, and any file's bytes on demand.
///
/// ```dart
/// final a = ArchiveContents.decode(bytes, name: 'snapshot.tar.gz');
/// a.children('');            // top-level folders and files
/// a.bytes('scratch/notes.txt');
/// ```
///
/// Reads zip (and jar/whl/docx-style zips), tar, and tar wrapped in gzip,
/// bzip2 or xz; a lone .gz/.bz2/.xz decodes to one file. The format is taken
/// from the bytes (magic numbers), not the name. Folders implied by entry
/// paths are listed even when the archive has no entry for them. Paths are
/// normalised to forward slashes with no leading `./` or `/`; entries that
/// climb out (`..`) are dropped.
library;

import 'dart:typed_data';

import 'package:archive/archive.dart';

/// The bytes are not an archive this reader understands, or it is too big.
class ArchiveContentsException implements Exception {
  /// Creates the exception.
  const ArchiveContentsException(this.message);

  /// What went wrong.
  final String message;

  @override
  String toString() => 'ArchiveContentsException: $message';
}

/// One file or folder in an archive.
class ArchiveEntry {
  /// Creates an entry.
  const ArchiveEntry({
    required this.path,
    required this.isDir,
    this.size = 0,
    this.modified,
    this.link,
  });

  /// Full path inside the archive, forward slashes, no trailing slash.
  final String path;

  /// A folder (explicit or implied by a deeper path).
  final bool isDir;

  /// Uncompressed size in bytes (0 for folders).
  final int size;

  /// Last change, when the archive records one.
  final DateTime? modified;

  /// Target of a symbolic link, when this is one.
  final String? link;

  /// The last path segment.
  String get name => path.substring(path.lastIndexOf('/') + 1);
}

/// A decoded archive.
class ArchiveContents {
  ArchiveContents._(this.format, this._entries, this._files);

  /// Decodes [bytes]. [name] only names a lone compressed file (its inner
  /// name is [name] without the .gz/.bz2/.xz). Throws
  /// [ArchiveContentsException] for an unknown format, a corrupt archive,
  /// or when unpacking a compressed stream would exceed [maxBytes].
  factory ArchiveContents.decode(Uint8List bytes, {String name = 'file', int maxBytes = 512 << 20}) {
    try {
      return _decode(bytes, name, maxBytes);
    } on ArchiveContentsException {
      rethrow;
    } catch (e) {
      throw ArchiveContentsException('could not read the archive: $e');
    }
  }

  /// 'zip', 'tar', 'tar.gz', 'tar.bz2', 'tar.xz', 'gz', 'bz2' or 'xz'.
  final String format;

  final Map<String, ArchiveEntry> _entries;
  final Map<String, ArchiveFile> _files;

  /// Every entry, folders first then by path.
  List<ArchiveEntry> get entries => _sorted(_entries.values);

  /// Files (not folders) in the archive.
  int get fileCount => _entries.values.where((e) => !e.isDir).length;

  /// Total uncompressed size of the files.
  int get totalSize => _entries.values.fold(0, (s, e) => s + e.size);

  /// The entry at [path], or null.
  ArchiveEntry? entry(String path) => _entries[_norm(path)];

  /// The folders and files directly inside folder [dir] ('' is the top),
  /// folders first, then by name.
  List<ArchiveEntry> children(String dir) {
    final d = _norm(dir);
    final prefix = d.isEmpty ? '' : '$d/';
    return _sorted(_entries.values.where((e) =>
        e.path.startsWith(prefix) && e.path.length > prefix.length && !e.path.substring(prefix.length).contains('/')));
  }

  /// The bytes of the file at [path], or null for a folder or a missing path.
  Uint8List? bytes(String path) {
    final f = _files[_norm(path)];
    if (f == null) return null;
    try {
      return f.readBytes() ?? Uint8List(0);
    } catch (e) {
      throw ArchiveContentsException('could not read ${_norm(path)}: $e');
    }
  }

  static List<ArchiveEntry> _sorted(Iterable<ArchiveEntry> es) => es.toList()
    ..sort((a, b) => a.isDir != b.isDir ? (a.isDir ? -1 : 1) : a.path.toLowerCase().compareTo(b.path.toLowerCase()));

  static String _norm(String p) {
    final parts = <String>[];
    for (final s in p.replaceAll('\\', '/').split('/')) {
      if (s.isEmpty || s == '.') continue;
      if (s == '..') return '';
      parts.add(s);
    }
    return parts.join('/');
  }

  static bool _isZip(Uint8List b) => b.length >= 4 && b[0] == 0x50 && b[1] == 0x4B && (b[2] == 3 || b[2] == 5 || b[2] == 7);
  static bool _isGz(Uint8List b) => b.length >= 2 && b[0] == 0x1F && b[1] == 0x8B;
  static bool _isBz2(Uint8List b) => b.length >= 3 && b[0] == 0x42 && b[1] == 0x5A && b[2] == 0x68;
  static bool _isXz(Uint8List b) =>
      b.length >= 6 && b[0] == 0xFD && b[1] == 0x37 && b[2] == 0x7A && b[3] == 0x58 && b[4] == 0x5A && b[5] == 0;
  static bool _isTar(Uint8List b) =>
      b.length >= 262 && String.fromCharCodes(b.sublist(257, 262)) == 'ustar' ||
      // Old (v7) tars have no magic: a plausible first header checksum.
      b.length >= 512 && _v7Checksum(b);

  static bool _v7Checksum(Uint8List b) {
    final field = String.fromCharCodes(b.sublist(148, 156)).replaceAll(RegExp(r'[\x00 ]'), '');
    final want = int.tryParse(field, radix: 8);
    if (want == null || b[0] == 0) return false;
    var sum = 0;
    for (var i = 0; i < 512; i++) {
      sum += (i >= 148 && i < 156) ? 0x20 : b[i];
    }
    return sum == want;
  }

  static ArchiveContents _decode(Uint8List bytes, String name, int maxBytes) {
    if (_isZip(bytes)) return _of('zip', ZipDecoder().decodeBytes(bytes), zip: true);
    if (_isTar(bytes)) return _of('tar', TarDecoder().decodeBytes(bytes));
    final (kind, inner) = _isGz(bytes)
        ? ('gz', GZipDecoder().decodeBytes(bytes))
        : _isBz2(bytes)
            ? ('bz2', BZip2Decoder().decodeBytes(bytes))
            : _isXz(bytes)
                ? ('xz', XZDecoder().decodeBytes(bytes))
                : ('', Uint8List(0));
    if (kind.isEmpty) throw const ArchiveContentsException('not a zip, tar, gzip, bzip2 or xz file');
    if (inner.length > maxBytes) {
      throw ArchiveContentsException('unpacks to ${inner.length} bytes, over the $maxBytes limit');
    }
    if (_isTar(inner)) return _of('tar.$kind', TarDecoder().decodeBytes(inner));
    final base = _norm(name).split('/').last;
    final lone = base.toLowerCase().endsWith('.$kind') ? base.substring(0, base.length - kind.length - 1) : base;
    return ArchiveContents._(
      kind,
      {lone: ArchiveEntry(path: lone, isDir: false, size: inner.length)},
      {lone: ArchiveFile.bytes(lone, inner)},
    );
  }

  static ArchiveContents _of(String format, Archive a, {bool zip = false}) {
    final entries = <String, ArchiveEntry>{};
    final files = <String, ArchiveFile>{};
    void folder(String p) {
      while (p.isNotEmpty && !entries.containsKey(p)) {
        entries[p] = ArchiveEntry(path: p, isDir: true);
        final i = p.lastIndexOf('/');
        p = i < 0 ? '' : p.substring(0, i);
      }
    }

    for (final f in a.files) {
      final raw = f.name.replaceAll('\\', '/');
      if (raw.split('/').contains('..')) continue;
      final p = _norm(raw);
      if (p.isEmpty) continue;
      final i = p.lastIndexOf('/');
      if (i > 0) folder(p.substring(0, i));
      DateTime? at;
      try {
        at = zip ? f.lastModDateTime : DateTime.fromMillisecondsSinceEpoch(f.lastModTime * 1000, isUtc: true);
      } catch (_) {}
      if (!f.isFile) {
        entries[p] = ArchiveEntry(path: p, isDir: true, modified: at);
        continue;
      }
      entries[p] = ArchiveEntry(
        path: p,
        isDir: false,
        size: f.size,
        modified: at,
        link: f.isSymbolicLink ? f.symbolicLink : null,
      );
      files[p] = f;
    }
    return ArchiveContents._(format, entries, files);
  }
}
