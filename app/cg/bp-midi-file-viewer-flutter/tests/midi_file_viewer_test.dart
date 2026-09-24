import 'dart:typed_data';

import 'package:flutter/gestures.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:midi_file_viewer/midi_file_viewer.dart';

List<int> vlq(int v) {
  final out = <int>[v & 0x7F];
  v >>= 7;
  while (v > 0) {
    out.insert(0, (v & 0x7F) | 0x80);
    v >>= 7;
  }
  return out;
}

List<int> u32(int v) => [v >> 24 & 0xFF, v >> 16 & 0xFF, v >> 8 & 0xFF, v & 0xFF];

List<int> track(List<(int, List<int>)> events) {
  final body = <int>[for (final (d, e) in events) ...[...vlq(d), ...e], 0, 0xFF, 0x2F, 0];
  return [...'MTrk'.codeUnits, ...u32(body.length), ...body];
}

Uint8List smf(List<List<int>> tracks) => Uint8List.fromList([
      ...'MThd'.codeUnits, ...u32(6), 0, 1, 0, tracks.length, 0x01, 0xE0, // 480 tpq
      for (final t in tracks) ...t,
    ]);

List<int> meta(int type, List<int> data) => [0xFF, type, ...vlq(data.length), ...data];
List<int> tempo(int us) => meta(0x51, [us >> 16 & 0xFF, us >> 8 & 0xFF, us & 0xFF]);

/// Conductor (3/4, B-flat major, a tempo change), a violin with pedal,
/// expression and bend, a bass on channel 3 with only a flat volume, drums.
Uint8List band({bool slows = true}) => smf([
      track([
        (0, meta(0x03, 'Song'.codeUnits)),
        (0, tempo(500000)),
        (0, meta(0x58, [3, 2, 24, 8])),
        (0, meta(0x59, [0xFE, 0])),
        if (slows) (1440, tempo(600000)),
      ]),
      track([
        (0, meta(0x03, 'Melody'.codeUnits)),
        (0, [0xC0, 40]),
        (0, [0xB0, 64, 127]),
        (0, [0xB0, 11, 80]),
        (0, [0x90, 70, 100]),
        (480, [0x80, 70, 0]),
        (0, [0xB0, 64, 0]),
        (0, [0xB0, 11, 110]),
        (0, [0xE0, 0, 0x50]),
        (0, [0x90, 74, 60]),
        (0, [0xE0, 0, 0x40]),
        (0, [0xB0, 64, 100]),
        (960, [0x80, 74, 0]),
        (0, [0xB0, 66, 127]),
        (240, [0xB0, 66, 0]),
      ]),
      track([
        (0, [0xC2, 33]),
        (0, [0xB2, 7, 100]),
        (0, [0x92, 46, 90]),
        (2880, [0x82, 46, 0]),
      ]),
      track([
        (0, meta(0x03, '  '.codeUnits)),
        (0, [0x99, 36, 110]),
        (60, [0x89, 36, 0]),
        (420, [0x99, 42, 70]),
        (60, [0x89, 42, 0]),
        (420, [0x99, 38, 100]),
        (60, [0x89, 38, 0]),
      ]),
    ]);

Future<void> pumpViewer(WidgetTester t, Widget w) async {
  t.view.physicalSize = const Size(1000, 700);
  t.view.devicePixelRatio = 1;
  addTearDown(t.view.reset);
  await t.pumpWidget(Directionality(textDirection: TextDirection.ltr, child: w));
}

/// Right of the 200px parts panel, below the header.
Rect rollArea(WidgetTester t) =>
    Rect.fromLTRB(201, t.getRect(find.text('Seconds')).bottom + 4, 1000, 420);

void main() {
  test('overview: parts, names, summary, lanes and axis', () {
    final o = MidiOverview.read(band());
    expect(o.parts.map((p) => p.instrument), ['Violin', 'Electric Bass (finger)', 'Drums']);
    expect(o.parts.map((p) => p.trackName), ['Melody', null, null]);
    expect(o.parts.map((p) => p.channel), [0, 2, 9]);
    expect(o.parts.map((p) => p.noteCount), [2, 1, 3]);
    expect(o.parts.last.isDrums, isTrue);
    expect(o.parts.first.program, 40);
    expect(o.parts[1].track, 2);
    expect(o.parts[1].index, 1);
    final s = o.summary;
    expect(s.bars, 2);
    expect(s.meter, '3/4');
    expect(s.key, 'B♭ major');
    expect(s.pedalPresses, 2);
    expect(s.warnings, isEmpty);
    expect(s.line, '0:03 · 2 bars · 3/4 · B♭ major · 100–120 bpm · 3 parts · 6 notes · 2 pedal presses');
    expect(s.toString(), s.line);
    expect(o.preferredAxis, MidiAxis.seconds);
    // Flat volume is setup, not a lane; the bend and expression move.
    expect(o.laneNames, ['Vel', 'Tempo', 'Ped 1', 'Expr 1', 'Sost 1', 'Bend 1']);
  });

  test('steady tempo prefers bars; minimal file summary', () {
    expect(MidiOverview.read(band(slows: false)).preferredAxis, MidiAxis.bars);
    expect(MidiOverview.read(band(), tempoTolerance: 0.5).preferredAxis, MidiAxis.bars);
    final one = MidiOverview.read(smf([
      track([(0, [0x90, 60, 1]), (480, [0x80, 60, 0])]),
    ]));
    expect(one.summary.line, '0:00 · 1 bar · 4/4 · 120 bpm · 1 part · 1 note');
    expect(one.laneNames, ['Vel']);
    final long = MidiOverview.read(smf([
      track([
        (0, meta(0x58, [4, 2, 24, 8])),
        (0, meta(0x58, [3, 2, 24, 8])),
        for (var i = 0; i < 1001; i++) ...[(0, [0x90, 60, 1]), (4000, [0x80, 60, 0])],
        (0, tempo(2000)), // 30000 bpm for the last note: over an hour of notes first
      ]),
    ]));
    expect(long.summary.line, startsWith('1:09:30 · '));
    expect(long.summary.line, contains('1,001 notes'));
    expect(long.summary.meterChanges, 1);
  });

  test('not a MIDI file', () {
    expect(() => MidiOverview.read(Uint8List.fromList([1, 2, 3])), throwsFormatException);
  });

  testWidgets('viewer lays out, toggles axis and parts, hovers a note', (t) async {
    await pumpViewer(t, MidiFileViewer(bytes: band()));
    expect(find.textContaining('B♭ major'), findsOneWidget);
    expect(find.text('Violin'), findsOneWidget);
    expect(find.text('Drums'), findsOneWidget);
    expect(find.text('Melody · ch 1 · 2 notes'), findsOneWidget);
    expect(find.text('Tempo'), findsOneWidget);
    expect(find.text('Ped 1'), findsOneWidget);

    await t.tap(find.text('Bars'));
    await t.pump();
    await t.tap(find.text('Bars')); // no-op when already selected
    await t.pump();
    await t.tap(find.text('Seconds'));
    await t.pump();

    await t.tap(find.text('Violin'));
    await t.pump();
    await t.tap(find.text('Violin'));
    await t.pump();

    // Sweep the pointer over the roll until a hover card appears.
    final box = rollArea(t);
    final mouse = await t.createGesture(kind: PointerDeviceKind.mouse);
    await mouse.addPointer(location: Offset.zero);
    var found = false;
    for (var y = box.top + 25; y < box.bottom && !found; y += 3) {
      await mouse.moveTo(Offset(box.left + 60, y));
      await t.pump();
      found = find.textContaining('Violin\nbar').evaluate().isNotEmpty;
    }
    expect(found, isTrue);
    expect(find.textContaining('B♭4 · Violin'), findsOneWidget);
    await mouse.removePointer();

    // Lanes scroll the shared viewport.
    await t.sendEventToBinding(PointerScrollEvent(
        position: t.getCenter(find.text('Tempo')) + const Offset(300, 0),
        scrollDelta: const Offset(40, 0)));
    await t.pump();

    // New bytes reload; an explicit axis wins.
    await pumpViewer(t, MidiFileViewer(bytes: band(slows: false), initialAxis: MidiAxis.seconds));
    expect(find.text('Tempo'), findsNothing);
    expect(t.takeException(), isNull);
  });

  testWidgets('drums alone fill the view; bad bytes explain', (t) async {
    await pumpViewer(t, MidiFileViewer(bytes: smf([
      track([(0, [0x99, 36, 100]), (10, [0x89, 36, 0]), (0, [0x99, 90, 100]), (10, [0x89, 90, 0])]),
    ])));
    expect(find.text('Drums'), findsOneWidget);
    final mouse = await t.createGesture(kind: PointerDeviceKind.mouse);
    await mouse.addPointer(location: Offset.zero);
    final box = rollArea(t);
    var found = false;
    for (var y = box.top + 5; y < box.bottom && !found; y += 10) {
      await mouse.moveTo(Offset(box.left + 45, y));
      await t.pump();
      found = find.textContaining('Kick · Drums').evaluate().isNotEmpty ||
          find.textContaining('Note 90 · Drums').evaluate().isNotEmpty;
    }
    expect(found, isTrue);
    await mouse.removePointer();

    await pumpViewer(t, MidiFileViewer(bytes: Uint8List.fromList('nope'.codeUnits)));
    expect(find.textContaining('Not a MIDI file'), findsOneWidget);
  });
}
