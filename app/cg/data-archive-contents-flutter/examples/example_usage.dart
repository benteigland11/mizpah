import 'dart:convert';
import 'dart:typed_data';

import 'package:archive/archive.dart';
import 'package:archive_contents/archive_contents.dart';
import 'package:flutter_test/flutter_test.dart';

/// Browse a gzipped tarball like a folder and read one file from it.
void main() {
  test('browse a tar.gz', () {
    final tar = Archive()
      ..addFile(ArchiveFile.string('project/README.md', '# Example'))
      ..addFile(ArchiveFile.string('project/data/items.csv', 'id,value\n1,2\n'));
    final bytes = Uint8List.fromList(GZipEncoder().encodeBytes(TarEncoder().encode(tar)));

    final contents = ArchiveContents.decode(bytes, name: 'example.tar.gz');
    expect(contents.format, 'tar.gz');
    expect(contents.children('project').map((e) => e.name), ['data', 'README.md']);
    expect(utf8.decode(contents.bytes('project/README.md')!), '# Example');
  });
}
