import 'dart:typed_data';

import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:midi_file_viewer/midi_file_viewer.dart';

/// Open a small two-part file: read its summary, then show it whole.
void main() {
  testWidgets('open a MIDI file', (tester) async {
    // Format 0, 480 ticks per quarter: a piano C-E and a bass C on channel 2.
    final bytes = Uint8List.fromList([
      ...'MThd'.codeUnits, 0, 0, 0, 6, 0, 0, 0, 1, 0x01, 0xE0,
      ...'MTrk'.codeUnits, 0, 0, 0, 31,
      0x00, 0xC1, 32, // channel 2: Acoustic Bass
      0x00, 0x90, 60, 90, // piano C4
      0x00, 0x91, 36, 80, // bass C2
      0x83, 0x60, 0x80, 60, 0, // after a beat, C4 off
      0x00, 0x90, 64, 70, // E4
      0x83, 0x60, 64, 0, // E4 off (running status)
      0x00, 0x81, 36, 0, // bass off
      0x00, 0xFF, 0x2F, 0,
    ]);

    final overview = MidiOverview.read(bytes);
    expect(overview.summary.line, '0:01 · 1 bar · 4/4 · 120 bpm · 2 parts · 3 notes');
    expect(overview.parts.map((p) => p.instrument), ['Acoustic Grand Piano', 'Acoustic Bass']);

    tester.view.physicalSize = const Size(900, 500);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(Directionality(
      textDirection: TextDirection.ltr,
      child: MidiFileViewer(bytes: bytes),
    ));
    expect(find.text('Acoustic Bass'), findsOneWidget);
  });
}
