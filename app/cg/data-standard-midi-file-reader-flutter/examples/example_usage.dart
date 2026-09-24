import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:standard_midi_file_reader/standard_midi_file_reader.dart';

/// Read a two-note file that slows down halfway, and place each note in
/// seconds and in bar:beat.
void main() {
  test('read a file and place its notes in time', () {
    // Header: format 1, one track, 480 ticks per quarter.
    final bytes = Uint8List.fromList([
      ...'MThd'.codeUnits, 0, 0, 0, 6, 0, 1, 0, 1, 0x01, 0xE0,
      ...'MTrk'.codeUnits, 0, 0, 0, 35,
      0x00, 0xFF, 0x51, 3, 0x07, 0xA1, 0x20, // 120 bpm
      0x00, 0x90, 60, 96, // C4 on
      0x83, 0x60, 0x80, 60, 0, // after 480 ticks, off
      0x00, 0xFF, 0x51, 3, 0x0F, 0x42, 0x40, // 60 bpm
      0x00, 0x90, 67, 80, // G4 on (beat 2)
      0x83, 0x60, 67, 0, // running status off
      0x00, 0xFF, 0x2F, 0,
    ]);
    final file = readMidi(bytes);
    final lines = [
      for (final n in file.notes)
        '${n.pitch} at ${file.timeMap.seconds(n.startTick)}s, '
            'bar:beat ${file.timeMap.barBeat(n.startTick)}',
    ];
    expect(lines, ['60 at 0.0s, bar:beat 1:1', '67 at 0.5s, bar:beat 1:2']);
    expect(file.durationSeconds, 1.5);
  });
}
