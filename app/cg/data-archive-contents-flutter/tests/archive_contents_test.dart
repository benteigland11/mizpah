import 'dart:convert';
import 'dart:typed_data';

import 'package:archive/archive.dart';
import 'package:archive_contents/archive_contents.dart';
import 'package:flutter_test/flutter_test.dart';

Archive sample({bool link = true}) {
  final a = Archive();
  a.addFile(ArchiveFile.string('scratch/notes.txt', 'hello'));
  a.addFile(ArchiveFile.string('./scratch/deep/x.json', '{"a": 1}'));
  a.addFile(ArchiveFile.string('piece.mid', 'MThd'));
  a.addFile(ArchiveFile.directory('empty/'));
  a.addFile(ArchiveFile.string('../escape.txt', 'no'));
  if (link) a.addFile(ArchiveFile.symlink('latest', 'piece.mid'));
  return a;
}

// The zip encoder cannot write a symbolic link; tars carry one.
Uint8List zip() => Uint8List.fromList(ZipEncoder().encode(sample(link: false)));
Uint8List tar() => Uint8List.fromList(TarEncoder().encode(sample()));

void main() {
  test('zip: tree, implied folders, bytes, dropped escapes', () {
    final c = ArchiveContents.decode(zip());
    expect(c.format, 'zip');
    expect(c.children('').map((e) => '${e.name}${e.isDir ? '/' : ''}'),
        ['empty/', 'scratch/', 'piece.mid']);
    expect(c.children('scratch').map((e) => e.name), ['deep', 'notes.txt']);
    expect(c.children('/scratch/deep/').single.path, 'scratch/deep/x.json');
    expect(utf8.decode(c.bytes('scratch/notes.txt')!), 'hello');
    expect(c.bytes('scratch'), isNull);
    expect(c.bytes('nope'), isNull);
    expect(c.entry('../escape.txt'), isNull);
    expect(c.entries.any((e) => e.path.contains('escape')), isFalse);
    expect(c.entry('scratch/notes.txt')!.size, 5);
    expect(c.entry('scratch/notes.txt')!.modified, isNotNull);
    expect(c.fileCount, 3);
    expect(c.totalSize, greaterThanOrEqualTo(5 + 8 + 4));
  });

  test('tar and compressed tars', () {
    final t = tar();
    expect(ArchiveContents.decode(t).format, 'tar');
    final gz = Uint8List.fromList(GZipEncoder().encodeBytes(t));
    final bz = Uint8List.fromList(BZip2Encoder().encodeBytes(t));
    final xz = Uint8List.fromList(XZEncoder().encodeBytes(t));
    for (final (bytes, f) in [(gz, 'tar.gz'), (bz, 'tar.bz2'), (xz, 'tar.xz')]) {
      final c = ArchiveContents.decode(bytes);
      expect(c.format, f);
      expect(utf8.decode(c.bytes('scratch/deep/x.json')!), '{"a": 1}');
      expect(c.entry('latest')!.link, 'piece.mid');
    }
  });

  test('a lone compressed file is one entry named without its suffix', () {
    final gz = Uint8List.fromList(GZipEncoder().encodeBytes(utf8.encode('just text')));
    final c = ArchiveContents.decode(gz, name: 'logs/run.log.gz');
    expect(c.format, 'gz');
    expect(c.children('').single.path, 'run.log');
    expect(utf8.decode(c.bytes('run.log')!), 'just text');
    expect(ArchiveContents.decode(gz, name: 'data').children('').single.path, 'data');
    expect(() => ArchiveContents.decode(gz, maxBytes: 3),
        throwsA(isA<ArchiveContentsException>().having((e) => '$e', 'message', contains('limit'))));
  });

  test('unknown or corrupt bytes throw', () {
    expect(() => ArchiveContents.decode(Uint8List.fromList(utf8.encode('plain'))),
        throwsA(isA<ArchiveContentsException>()));
    final broken = Uint8List.fromList([0x1F, 0x8B, 8, 0, 0, 0, 0, 0, 0, 3, 0xFF, 0xFF, 0xFF, 0xFF]);
    expect(() => ArchiveContents.decode(broken), throwsA(isA<ArchiveContentsException>()));
    // A truncated zip is read leniently: what survives, possibly nothing.
    expect(ArchiveContents.decode(zip().sublist(0, 30)).format, 'zip');
  });

  test('old v7 tar without magic is recognised by its checksum', () {
    final t = tar();
    // Blank the ustar magic and version, then fix the header checksum.
    for (var i = 257; i < 265; i++) {
      t[i] = 0;
    }
    for (var i = 148; i < 156; i++) {
      t[i] = 0x20;
    }
    var sum = 0;
    for (var i = 0; i < 512; i++) {
      sum += t[i];
    }
    final field = '${sum.toRadixString(8).padLeft(6, '0')}\u0000 ';
    for (var i = 0; i < 8; i++) {
      t[148 + i] = field.codeUnitAt(i);
    }
    expect(ArchiveContents.decode(t).format, 'tar');
  });

  test('entry names', () {
    const e = ArchiveEntry(path: 'a/b/c.txt', isDir: false);
    expect(e.name, 'c.txt');
    expect(const ArchiveEntry(path: 'top', isDir: true).name, 'top');
    expect(const ArchiveContentsException('x').toString(), contains('x'));
  });
}
