// A role signs; a name is a courtesy. With no name set, the title alone is
// the line on the paper, the name line of the block, and what the hand font
// sets when there is no mark — the same split a stored line gets back.
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mizpah_app/engine/file_settings_store.dart';
import 'package:mizpah_app/state/app_settings.dart';

void main() {
  late Directory tmp;
  setUp(() => tmp = Directory.systemTemp.createTempSync('signer'));
  tearDown(() => tmp.deleteSync(recursive: true));

  Future<AppSettings> settings() async {
    final s = AppSettings(FileSettingsStore(file: File('${tmp.path}/app.json')));
    await s.ready;
    return s;
  }

  test('the default title alone is enough to sign', () async {
    final s = await settings();
    expect(s.signerName, '');
    expect(s.canSign, isTrue);
    expect(s.signerLine, 'Administrator');
    expect(s.signerParts, ('Administrator', ''));
    expect(s.markName, 'Administrator');
  });

  test('a name goes first, the title after it', () async {
    final s = await settings();
    s.setSigner(name: 'Ben Teigland');
    expect(s.signerLine, 'Ben Teigland, Administrator');
    expect(s.signerParts, ('Ben Teigland', 'Administrator'));
    expect(s.markName, 'Ben Teigland');
  });

  test('nothing to sign as means no signing', () async {
    final s = await settings();
    s.setSigner(title: '  ');
    expect(s.canSign, isFalse);
    expect(s.signerLine, '');
  });
}
