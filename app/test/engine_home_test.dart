import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mizpah_app/engine/engine_home.dart';

void main() {
  late Directory tmp;
  setUp(() => tmp = Directory.systemTemp.createTempSync('eh'));
  tearDown(() => tmp.deleteSync(recursive: true));

  Directory plant(String root) {
    File('$root/.venv/bin/terra').createSync(recursive: true);
    Directory('$root/engine/mizpah').createSync(recursive: true);
    return Directory(root);
  }

  test('walks up from the executable to the repository root', () {
    final repo = plant('${tmp.path}/repo');
    final exe = '${repo.path}/app/build/linux/x64/debug/bundle/app';
    File(exe).createSync(recursive: true);
    final h = EngineHome.locate(executable: exe, cwd: tmp.path, environ: const {});
    expect(h?.root, repo.path);
    expect(h?.terra, '${repo.path}/.venv/bin/terra');
    expect(h?.config('config.openai.json'), '${repo.path}/engine/mizpah/config.openai.json');
  });

  test('a sidecar beside the executable wins over the tree above it', () {
    plant('${tmp.path}/repo');
    final bundle = '${tmp.path}/repo/dist';
    plant('$bundle/engine');
    final h = EngineHome.locate(executable: '$bundle/app', cwd: tmp.path, environ: const {});
    expect(h?.root, '$bundle/engine');
  });

  test('override and MIZPAH_ENGINE come first; nothing found is null', () {
    final a = plant('${tmp.path}/a'), b = plant('${tmp.path}/b');
    expect(EngineHome.locate(override: a.path, environ: {'MIZPAH_ENGINE': b.path}, executable: '${tmp.path}/x', cwd: tmp.path)?.root, a.path);
    expect(EngineHome.locate(environ: {'MIZPAH_ENGINE': b.path}, executable: '${tmp.path}/x', cwd: tmp.path)?.root, b.path);
    expect(EngineHome.locate(environ: const {}, executable: '${tmp.path}/x', cwd: tmp.path), isNull);
  });
}
