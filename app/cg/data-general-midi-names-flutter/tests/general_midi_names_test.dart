import 'package:flutter_test/flutter_test.dart';
import 'package:general_midi_names/general_midi_names.dart';

void main() {
  test('programs have names and families', () {
    expect(gmInstruments.length, 128);
    expect({for (final i in gmInstruments) i.name}.length, 128);
    expect(gmProgram(0).name, 'Acoustic Grand Piano');
    expect(gmProgram(40).family, GmFamily.strings);
    expect(gmProgram(40).toString(), 'Violin');
    expect(gmProgram(127).name, 'Gunshot');
    expect(gmProgram(127).family.label, 'Sound Effects');
    expect(gmProgram(200).program, 127);
    expect(gmProgram(-1).program, 0);
    for (final f in GmFamily.values) {
      expect(gmInstruments.where((i) => i.family == f).length, 8);
    }
  });

  test('percussion map', () {
    expect(gmDrum(36), 'Kick');
    expect(gmDrum(38), 'Acoustic Snare');
    expect(gmDrum(42), 'Closed Hi-Hat');
    expect(gmDrum(27), 'High Q');
    expect(gmDrum(87), 'Open Surdo');
    expect(gmDrum(26), isNull);
    expect(gmDrum(88), isNull);
    expect(isGmPercussionChannel(9), isTrue);
    expect(isGmPercussionChannel(0), isFalse);
  });

  test('pitch names follow the key', () {
    expect(pitchName(60), 'C4');
    expect(pitchName(21), 'A0');
    expect(pitchName(108), 'C8');
    expect(pitchName(0), 'C-1');
    expect(pitchName(63), 'D♯4');
    expect(pitchName(63, keySharps: -3), 'E♭4');
    expect(pitchName(63, keySharps: 2), 'D♯4');
    expect(pitchName(63, keySharps: -3, ascii: true), 'Eb4');
    expect(pitchName(61, ascii: true), 'C#4');
    expect(pitchName(60, middleCOctave: 3), 'C3');
    expect(pitchClassName(70, keySharps: -1), 'B♭');
    expect(isBlackKey(61), isTrue);
    expect(isBlackKey(64), isFalse);
  });

  test('key names', () {
    expect(keyName(0), 'C major');
    expect(keyName(0, minor: true), 'A minor');
    expect(keyName(-3), 'E♭ major');
    expect(keyName(-3, minor: true), 'C minor');
    expect(keyName(7), 'C♯ major');
    expect(keyName(-9), 'C♭ major');
    expect(keyName(6, minor: true, ascii: true), 'D# minor');
  });
}
