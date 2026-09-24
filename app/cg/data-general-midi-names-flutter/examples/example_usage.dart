import 'package:flutter_test/flutter_test.dart';
import 'package:general_midi_names/general_midi_names.dart';

/// Label the parts of a small arrangement: a program per melodic channel,
/// kit pieces on the percussion channel, and note names in the file's key.
void main() {
  test('label an arrangement', () {
    const parts = {0: 0, 1: 32, 9: 0}; // channel -> program
    final labels = [
      for (final e in parts.entries)
        isGmPercussionChannel(e.key)
            ? 'ch${e.key + 1}: Drums'
            : 'ch${e.key + 1}: ${gmProgram(e.value)} (${gmProgram(e.value).family.label})',
    ];
    expect(labels, [
      'ch1: Acoustic Grand Piano (Piano)',
      'ch2: Acoustic Bass (Bass)',
      'ch10: Drums',
    ]);
    expect([36, 38, 42].map(gmDrum), ['Kick', 'Acoustic Snare', 'Closed Hi-Hat']);
    // In E-flat major, black keys are spelled with flats.
    expect([63, 67, 70].map((n) => pitchName(n, keySharps: -3)), ['E♭4', 'G4', 'B♭4']);
    expect(keyName(-3), 'E♭ major');
  });
}
