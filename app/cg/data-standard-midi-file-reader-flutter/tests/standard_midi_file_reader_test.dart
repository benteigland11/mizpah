import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:standard_midi_file_reader/standard_midi_file_reader.dart';

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

/// A track chunk from (delta, bytes) events; end-of-track is appended.
List<int> track(List<(int, List<int>)> events, {bool end = true}) {
  final body = <int>[
    for (final (d, e) in events) ...[...vlq(d), ...e],
    if (end) ...[0, 0xFF, 0x2F, 0],
  ];
  return [...'MTrk'.codeUnits, ...u32(body.length), ...body];
}

Uint8List smf(List<List<int>> tracks, {int format = 1, int division = 480}) =>
    Uint8List.fromList([
      ...'MThd'.codeUnits, ...u32(6), 0, format, 0, tracks.length,
      division >> 8, division & 0xFF,
      for (final t in tracks) ...t,
    ]);

List<int> meta(int type, List<int> data) => [0xFF, type, ...vlq(data.length), ...data];
List<int> tempo(int us) => meta(0x51, [us >> 16 & 0xFF, us >> 8 & 0xFF, us & 0xFF]);

void main() {
  test('format 0 with running status and velocity-0 offs', () {
    final f = readMidi(smf(format: 0, [
      track([
        (0, meta(0x03, 'Piano'.codeUnits)),
        (0, [0x90, 60, 100]),
        (0, [64, 90]), // running status note-on
        (480, [60, 0]), // running status velocity-0 off
        (0, [64, 0]),
        (0, [0x80, 67, 40]), // off with nothing open: ignored
      ]),
    ]));
    expect(f.format, 0);
    expect(f.ticksPerQuarter, 480);
    expect(f.tracks.single.name, 'Piano');
    final n = f.tracks.single.notes;
    expect(n.map((x) => x.pitch), [60, 64]);
    expect(n.map((x) => x.durationTicks), [480, 480]);
    expect(n.first.velocity, 100);
    expect(f.warnings, isEmpty);
  });

  test('overlapping repeats of a pitch close oldest-first', () {
    final f = readMidi(smf([
      track([
        (0, [0x91, 60, 80]),
        (100, [0x91, 60, 90]),
        (100, [0x81, 60, 10]),
        (100, [0x81, 60, 20]),
      ]),
    ]));
    final n = f.tracks.single.notes;
    expect(n.map((x) => (x.startTick, x.endTick, x.velocity, x.offVelocity)),
        [(0, 200, 80, 10), (100, 300, 90, 20)]);
    expect(n.first.channel, 1);
    expect(f.tracks.single.channels, [1]);
  });

  test('notes left open close at end of track', () {
    final f = readMidi(smf([
      track([(0, [0x90, 50, 70]), (960, meta(0x01, 'x'.codeUnits))]),
    ]));
    expect(f.tracks.single.notes.single.endTick, 960);
    expect(f.lengthTicks, 960);
  });

  test('controls, bends, programs, sysex and aftertouch', () {
    final f = readMidi(smf([
      track([
        (0, [0xC9, 0]),
        (0, [0xB0, 64, 127]),
        (0, [0xF0, 3, 0x7E, 0x09, 0xF7]), // sysex cancels running status
        (10, [0xE0, 0, 0x40]), // centre
        (0, [0xE0, 0x7F, 0x7F]), // max
        (0, [0xA0, 60, 5]),
        (0, [0xD0, 7]),
        (5, [0xB0, 64, 0]),
        (0, [0xF7, 1, 0x01]), // escape
        (0, [0xF6]), // tune request, no data
        (0, [0xF2, 1, 2]),
        (0, [0xF3, 1]),
      ]),
    ]));
    final t = f.tracks.single;
    expect(t.programs.single.channel, 9);
    expect(t.programs.single.program, 0);
    expect(t.controls.map((c) => (c.tick, c.controller, c.value)),
        [(0, 64, 127), (15, 64, 0)]);
    expect(t.pitchBends.map((b) => b.value), [0, 8191]);
  });

  test('tempo, meter and key from a conductor track; texts', () {
    final f = readMidi(smf([
      track([
        (0, tempo(500000)),
        (0, meta(0x58, [3, 2, 24, 8])),
        (0, meta(0x59, [0xFD, 1])), // 3 flats, minor
        (0, meta(0x06, 'A'.codeUnits)),
        (960, tempo(1000000)),
        (0, meta(0x05, [0xE2, 0x99, 0xAA])), // valid UTF-8
        (0, meta(0x05, [0xE9])), // Latin-1 fallback
        (0, meta(0x0A, 'misc'.codeUnits)),
      ]),
      track([(0, meta(0x04, 'Strings'.codeUnits)), (0, [0x90, 60, 1]), (1920, [0x80, 60, 0])]),
    ]));
    expect(f.tempos.map((t) => t.bpm), [120, 60]);
    expect(f.timeSignatures.single.toString(), '3/4');
    expect(f.timeSignatures.single.clocksPerClick, 24);
    expect(f.keySignatures.single.sharps, -3);
    expect(f.keySignatures.single.minor, isTrue);
    expect(f.markers.single.text, 'A');
    expect(f.lyrics.map((l) => l.text), ['♪', 'é']);
    expect(f.tracks[0].texts.last.kind, MidiTextKind.other);
    expect(f.tracks[1].instrumentName, 'Strings');
    expect(f.notes.length, 1);
    // 960 ticks at 120 = 1 s, then 960 at 60 = 2 s.
    expect(f.durationSeconds, closeTo(3.0, 1e-9));
    expect(f.timeMap.bpmAt(0), closeTo(120, 1e-9));
    expect(f.timeMap.bpmAt(1000), closeTo(60, 1e-9));
    expect(f.timeMap.tickAt(1.0), 960);
    expect(f.timeMap.tickAt(2.0), 1440);
  });

  test('bar:beat follows meter changes, including a mid-bar change', () {
    final map = MidiTimeMap(ticksPerQuarter: 480, timeSignatures: const [
      MidiTimeSignature(0, 4, 4),
      MidiTimeSignature(1920, 6, 8),
      MidiTimeSignature(1920 + 1440 + 240, 2, 4), // lands mid-bar
    ]);
    expect(map.barBeat(0).toString(), '1:1');
    expect(map.barBeat(480 * 3 + 240).toString(), '1:4');
    expect(map.barBeat(480 * 3 + 240).fraction, 0.5);
    expect(map.barBeat(1920).toString(), '2:1');
    expect(map.barBeat(1920 + 240 * 5).toString(), '2:6');
    expect(map.barBeat(1920 + 1440).toString(), '3:1');
    expect(map.barBeat(3600).toString(), '4:1');
    expect(map.tickOfBar(3), 3360);
    expect(map.tickOfBar(5), 3600 + 960);
    expect(map.barCount(3600), 3);
    expect(map.barCount(0), 0);
    final lines = map.gridLines(1920);
    expect(lines.where((l) => l.isBar).map((l) => l.tick), [0, 1920]);
    expect(lines.length, 5);
    final six = map.gridLines(3360).where((l) => l.bar == 2).toList();
    expect(six.length, 6);
  });

  test('defaults: 120 bpm, 4/4, same-tick changes replace', () {
    final map = MidiTimeMap(ticksPerQuarter: 96, tempos: const [
      MidiTempo(0, 400000),
      MidiTempo(0, 600000),
    ], timeSignatures: const [MidiTimeSignature(0, 3, 4)]);
    expect(map.seconds(96), closeTo(0.6, 1e-9));
    expect(map.barBeat(96 * 3).bar, 2);
    expect(MidiTimeMap(ticksPerQuarter: 96).seconds(192), closeTo(1.0, 1e-9));
  });

  test('SMPTE division ignores tempo', () {
    final f = readMidi(smf(division: 0xE728, [ // -25 fps, 40 ticks per frame
      track([(0, tempo(1000000)), (1000, [0x90, 60, 1])]),
    ]));
    expect(f.smpteTicksPerSecond, 1000);
    expect(f.timeMap.seconds(1000), closeTo(1.0, 1e-9));
    final ntsc = readMidi(smf(division: 0xE302, [track([])]));
    expect(ntsc.smpteTicksPerSecond, closeTo(59.94, 1e-9));
  });

  test('truncation and stray bytes are warnings, not failures', () {
    final bytes = smf([
      track([(0, [0x40]), (0, [0x90, 60, 100]), (10, [0x80, 60])], end: false),
    ]);
    final f = readMidi(bytes);
    expect(f.tracks.single.notes.single.endTick, 10);
    expect(f.warnings.join('\n'), contains('without status'));
    expect(f.warnings.join('\n'), contains('mid-event'));

    final cut = readMidi(Uint8List.sublistView(
        smf([track([(0, [0x90, 60, 100]), (10, [0x80, 60, 0])])]), 0, 30));
    expect(cut.warnings.first, contains('truncated'));

    final extra = Uint8List.fromList([
      ...smf([track([])]).sublist(0, 11), 3, // declares three tracks
      ...smf([track([])]).sublist(12),
      ...'XFIH'.codeUnits, ...u32(2), 1, 2, // unknown chunk skipped
    ]);
    final e = readMidi(extra);
    expect(e.tracks.length, 1);
    expect(e.warnings.single, contains('declares 3'));
  });

  test('RIFF-wrapped files are unwrapped', () {
    final inner = smf([track([(0, [0x90, 60, 1]), (1, [0x80, 60, 0])])]);
    final rmi = Uint8List.fromList(
        [...'RIFF'.codeUnits, 0, 0, 0, 0, ...'RMIDdata'.codeUnits, 0, 0, 0, 0, ...inner]);
    expect(readMidi(rmi).notes.length, 1);
  });

  test('bad headers throw', () {
    expect(() => readMidi(Uint8List.fromList('nope'.codeUnits)),
        throwsA(isA<MidiFormatException>()));
    final bad = smf([track([])]);
    expect(() => readMidi(Uint8List.fromList([...bad]..[9] = 3)),
        throwsA(predicate((e) => '$e'.contains('format 3'))));
    expect(() => readMidi(Uint8List.fromList([...bad]..[12] = 0..[13] = 0)),
        throwsA(isA<MidiFormatException>()));
    expect(() => readMidi(Uint8List.fromList([...bad]..[7] = 2)),
        throwsA(isA<MidiFormatException>()));
  });
}
