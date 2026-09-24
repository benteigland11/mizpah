/// Standard MIDI File reader: bytes in, a typed model out.
///
/// ```dart
/// final file = readMidi(bytes);
/// file.timeMap.seconds(note.startTick);  // follows every tempo change
/// file.timeMap.barBeat(note.startTick);  // 3:2 in the meter at that tick
/// ```
///
/// Reads formats 0, 1 and 2 (format 2 tracks are read like format 1: the
/// tempo and meter events of every track share one map). Notes are paired
/// from note-on/off (velocity-0 note-ons are offs); overlapping repeats of a
/// pitch on a channel close oldest-first; notes still sounding at the end of
/// a track close there. Running status, variable-length numbers, sysex and
/// unknown chunks are handled; a truncated track keeps what was read and
/// adds a line to [MidiFile.warnings]. A bad header throws
/// [MidiFormatException].
library;

import 'dart:convert';
import 'dart:typed_data';

/// The bytes are not a Standard MIDI File.
class MidiFormatException implements Exception {
  /// Creates the exception.
  const MidiFormatException(this.message);

  /// What was wrong.
  final String message;

  @override
  String toString() => 'MidiFormatException: $message';
}

/// A sounding note, paired from its note-on and note-off.
class MidiNote {
  /// Creates a note.
  const MidiNote({
    required this.channel,
    required this.pitch,
    required this.velocity,
    required this.startTick,
    required this.endTick,
    this.offVelocity = 0,
  });

  /// Channel 0-15 (channel 10 in the usual 1-based naming is 9 here).
  final int channel;

  /// Note number 0-127 (60 is middle C).
  final int pitch;

  /// Note-on velocity 1-127.
  final int velocity;

  /// Release velocity from the note-off, 0 when there was none.
  final int offVelocity;

  /// Tick of the note-on.
  final int startTick;

  /// Tick of the note-off.
  final int endTick;

  /// Length in ticks.
  int get durationTicks => endTick - startTick;
}

/// A control change (CC) message.
class MidiControlChange {
  /// Creates a control change.
  const MidiControlChange(this.tick, this.channel, this.controller, this.value);

  /// Absolute tick.
  final int tick;

  /// Channel 0-15.
  final int channel;

  /// Controller number 0-127 (64 is the sustain pedal).
  final int controller;

  /// Value 0-127.
  final int value;
}

/// A pitch-bend message.
class MidiPitchBend {
  /// Creates a pitch bend.
  const MidiPitchBend(this.tick, this.channel, this.value);

  /// Absolute tick.
  final int tick;

  /// Channel 0-15.
  final int channel;

  /// Bend -8192..8191, 0 is centre.
  final int value;
}

/// A program (instrument) change.
class MidiProgramChange {
  /// Creates a program change.
  const MidiProgramChange(this.tick, this.channel, this.program);

  /// Absolute tick.
  final int tick;

  /// Channel 0-15.
  final int channel;

  /// Program 0-127.
  final int program;
}

/// A tempo change (meta 0x51).
class MidiTempo {
  /// Creates a tempo change.
  const MidiTempo(this.tick, this.microsecondsPerQuarter);

  /// Absolute tick.
  final int tick;

  /// Microseconds per quarter note.
  final int microsecondsPerQuarter;

  /// Quarter notes per minute.
  double get bpm => 60000000 / microsecondsPerQuarter;
}

/// A time signature (meta 0x58).
class MidiTimeSignature {
  /// Creates a time signature.
  const MidiTimeSignature(this.tick, this.numerator, this.denominator,
      {this.clocksPerClick = 24, this.thirtySecondsPerQuarter = 8});

  /// Absolute tick.
  final int tick;

  /// Beats per bar.
  final int numerator;

  /// Beat unit (4 = quarter, 8 = eighth).
  final int denominator;

  /// MIDI clocks per metronome click.
  final int clocksPerClick;

  /// Notated 32nd notes per MIDI quarter.
  final int thirtySecondsPerQuarter;

  @override
  String toString() => '$numerator/$denominator';
}

/// A key signature (meta 0x59).
class MidiKeySignature {
  /// Creates a key signature.
  const MidiKeySignature(this.tick, this.sharps, {this.minor = false});

  /// Absolute tick.
  final int tick;

  /// Sharps (positive) or flats (negative), -7..7.
  final int sharps;

  /// Minor mode.
  final bool minor;
}

/// Which text meta event a [MidiText] came from.
enum MidiTextKind {
  /// 0x01 free text.
  text,

  /// 0x02 copyright notice.
  copyright,

  /// 0x03 sequence or track name.
  trackName,

  /// 0x04 instrument name.
  instrumentName,

  /// 0x05 lyric.
  lyric,

  /// 0x06 marker.
  marker,

  /// 0x07 cue point.
  cuePoint,

  /// 0x08-0x0F other text types.
  other,
}

/// A text meta event.
class MidiText {
  /// Creates a text event.
  const MidiText(this.tick, this.kind, this.text);

  /// Absolute tick.
  final int tick;

  /// Which text event.
  final MidiTextKind kind;

  /// Decoded text (UTF-8, falling back to Latin-1).
  final String text;
}

/// One track chunk.
class MidiTrack {
  /// Creates a track.
  const MidiTrack({
    required this.index,
    this.name,
    this.instrumentName,
    this.notes = const [],
    this.controls = const [],
    this.pitchBends = const [],
    this.programs = const [],
    this.texts = const [],
    this.endTick = 0,
  });

  /// Position in the file, from 0.
  final int index;

  /// First track-name event, if any.
  final String? name;

  /// First instrument-name event, if any.
  final String? instrumentName;

  /// Notes sorted by start tick then pitch.
  final List<MidiNote> notes;

  /// Control changes in file order.
  final List<MidiControlChange> controls;

  /// Pitch bends in file order.
  final List<MidiPitchBend> pitchBends;

  /// Program changes in file order.
  final List<MidiProgramChange> programs;

  /// Every text meta event of the track, in order.
  final List<MidiText> texts;

  /// Tick of the last event (end of track).
  final int endTick;

  /// Channels the track's notes use, ascending.
  List<int> get channels => {for (final n in notes) n.channel}.toList()..sort();
}

/// Bar and beat of a tick, both counted from 1.
class MidiBarBeat {
  /// Creates a position.
  const MidiBarBeat(this.bar, this.beat, this.tickInBeat, this.ticksPerBeat);

  /// Bar number from 1.
  final int bar;

  /// Beat in the bar from 1.
  final int beat;

  /// Ticks past the start of the beat.
  final int tickInBeat;

  /// Length of a beat in this meter, in ticks.
  final int ticksPerBeat;

  /// Fraction of the beat elapsed, 0 <= f < 1.
  double get fraction => ticksPerBeat == 0 ? 0 : tickInBeat / ticksPerBeat;

  @override
  String toString() => '$bar:$beat';
}

/// A bar or beat line of the metrical grid.
class MidiGridLine {
  /// Creates a grid line.
  const MidiGridLine(this.tick, this.bar, this.beat);

  /// Absolute tick.
  final int tick;

  /// Bar number from 1.
  final int bar;

  /// Beat from 1; 1 is the bar line.
  final int beat;

  /// Whether this line starts a bar.
  bool get isBar => beat == 1;
}

class _TempoSegment {
  _TempoSegment(this.tick, this.seconds, this.secondsPerTick);
  final int tick;
  final double seconds;
  final double secondsPerTick;
}

class _MeterSegment {
  _MeterSegment(this.tick, this.bar, this.numerator, this.ticksPerBeat);
  final int tick;
  final int bar; // 0-based index of the bar that starts here
  final int numerator;
  final int ticksPerBeat;
  int get ticksPerBar => numerator * ticksPerBeat;
}

/// Converts ticks to seconds and to bar:beat across tempo and meter changes.
class MidiTimeMap {
  /// Builds a map for a file's division and its tempo and meter events.
  ///
  /// With [smpteTicksPerSecond] set (SMPTE division), ticks are fixed-length
  /// and tempo events do not change timing.
  MidiTimeMap({
    required this.ticksPerQuarter,
    List<MidiTempo> tempos = const [],
    List<MidiTimeSignature> timeSignatures = const [],
    this.smpteTicksPerSecond,
  }) {
    final tq = ticksPerQuarter <= 0 ? 1 : ticksPerQuarter;
    final sps = smpteTicksPerSecond;
    if (sps != null && sps > 0) {
      _tempo.add(_TempoSegment(0, 0, 1 / sps));
    } else {
      final ts = [...tempos]..sort((a, b) => a.tick.compareTo(b.tick));
      var seg = _TempoSegment(0, 0, _defaultTempo / 1e6 / tq);
      _tempo.add(seg);
      for (final t in ts) {
        final at = seg.seconds + (t.tick - seg.tick) * seg.secondsPerTick;
        seg = _TempoSegment(t.tick, at, t.microsecondsPerQuarter / 1e6 / tq);
        if (_tempo.last.tick == t.tick) _tempo.removeLast();
        _tempo.add(seg);
      }
    }
    final ms = [...timeSignatures]..sort((a, b) => a.tick.compareTo(b.tick));
    _meter.add(_MeterSegment(0, 0, 4, tq));
    for (final m in ms) {
      final last = _meter.last;
      final den = m.denominator <= 0 ? 4 : m.denominator;
      final beat = (tq * 4 / den).round().clamp(1, 1 << 30);
      final num = m.numerator <= 0 ? 4 : m.numerator;
      if (m.tick <= last.tick) {
        _meter[_meter.length - 1] = _MeterSegment(last.tick, last.bar, num, beat);
        continue;
      }
      // A change that falls mid-bar starts a new bar where it lands.
      final elapsed = m.tick - last.tick;
      final bars = (elapsed + last.ticksPerBar - 1) ~/ last.ticksPerBar;
      _meter.add(_MeterSegment(m.tick, last.bar + bars, num, beat));
    }
  }

  static const int _defaultTempo = 500000;

  /// Ticks per quarter note of the file.
  final int ticksPerQuarter;

  /// Ticks per second under SMPTE division, else null.
  final double? smpteTicksPerSecond;

  final List<_TempoSegment> _tempo = [];
  final List<_MeterSegment> _meter = [];

  static int _find<T>(List<T> segs, bool Function(T) after) {
    var lo = 0, hi = segs.length - 1;
    while (lo < hi) {
      final mid = (lo + hi + 1) >> 1;
      if (after(segs[mid])) {
        hi = mid - 1;
      } else {
        lo = mid;
      }
    }
    return lo;
  }

  /// Seconds from the start to [tick].
  double seconds(int tick) {
    final s = _tempo[_find<_TempoSegment>(_tempo, (x) => x.tick > tick)];
    return s.seconds + (tick - s.tick) * s.secondsPerTick;
  }

  /// The tick at [seconds] (inverse of [seconds]), rounded to the nearest.
  int tickAt(double seconds) {
    final s = _tempo[_find<_TempoSegment>(_tempo, (x) => x.seconds > seconds)];
    return s.tick + ((seconds - s.seconds) / s.secondsPerTick).round();
  }

  /// Tempo in quarter notes per minute at [tick].
  double bpmAt(int tick) {
    final s = _tempo[_find<_TempoSegment>(_tempo, (x) => x.tick > tick)];
    return 60 / (s.secondsPerTick * (ticksPerQuarter <= 0 ? 1 : ticksPerQuarter));
  }

  _MeterSegment _meterAt(int tick) =>
      _meter[_find<_MeterSegment>(_meter, (x) => x.tick > tick)];

  /// Bar and beat of [tick].
  MidiBarBeat barBeat(int tick) {
    final m = _meterAt(tick);
    final into = tick - m.tick;
    final bar = m.bar + into ~/ m.ticksPerBar;
    final inBar = into % m.ticksPerBar;
    return MidiBarBeat(bar + 1, inBar ~/ m.ticksPerBeat + 1,
        inBar % m.ticksPerBeat, m.ticksPerBeat);
  }

  /// Tick where [bar] (from 1) starts.
  int tickOfBar(int bar) {
    final b = bar - 1;
    final m = _meter[_find<_MeterSegment>(_meter, (x) => x.bar > b)];
    return m.tick + (b - m.bar) * m.ticksPerBar;
  }

  /// Bars needed to hold [endTick] (a partial last bar counts).
  int barCount(int endTick) {
    if (endTick <= 0) return 0;
    final bb = barBeat(endTick - 1);
    return bb.bar;
  }

  /// Bar and beat lines from 0 up to [endTick] inclusive.
  List<MidiGridLine> gridLines(int endTick) {
    final out = <MidiGridLine>[];
    for (var i = 0; i < _meter.length; i++) {
      final m = _meter[i];
      final stop = i + 1 < _meter.length ? _meter[i + 1].tick : endTick + 1;
      var bar = m.bar;
      for (var t = m.tick; t < stop && t <= endTick; bar++) {
        for (var beat = 0; beat < m.numerator; beat++) {
          final at = t + beat * m.ticksPerBeat;
          if (at >= stop || at > endTick) break;
          out.add(MidiGridLine(at, bar + 1, beat + 1));
        }
        t += m.ticksPerBar;
      }
    }
    return out;
  }
}

/// A whole file.
class MidiFile {
  /// Creates a file model.
  MidiFile({
    required this.format,
    required this.ticksPerQuarter,
    required this.tracks,
    this.tempos = const [],
    this.timeSignatures = const [],
    this.keySignatures = const [],
    this.warnings = const [],
    this.smpteTicksPerSecond,
  }) : timeMap = MidiTimeMap(
          ticksPerQuarter: ticksPerQuarter,
          tempos: tempos,
          timeSignatures: timeSignatures,
          smpteTicksPerSecond: smpteTicksPerSecond,
        );

  /// 0 (one track), 1 (parallel tracks) or 2 (independent sequences).
  final int format;

  /// Ticks per quarter note (under SMPTE division, ticks per frame).
  final int ticksPerQuarter;

  /// Ticks per second when the division is SMPTE, else null.
  final double? smpteTicksPerSecond;

  /// Track chunks in file order.
  final List<MidiTrack> tracks;

  /// Tempo changes from every track, by tick.
  final List<MidiTempo> tempos;

  /// Time signatures from every track, by tick.
  final List<MidiTimeSignature> timeSignatures;

  /// Key signatures from every track, by tick.
  final List<MidiKeySignature> keySignatures;

  /// Problems read past without failing (truncation, stray bytes).
  final List<String> warnings;

  /// Tick to seconds and tick to bar:beat.
  final MidiTimeMap timeMap;

  /// Every note of every track, by start tick.
  List<MidiNote> get notes =>
      [for (final t in tracks) ...t.notes]..sort(_byStart);

  /// Markers from every track, by tick.
  List<MidiText> get markers => _texts(MidiTextKind.marker);

  /// Lyrics from every track, by tick.
  List<MidiText> get lyrics => _texts(MidiTextKind.lyric);

  List<MidiText> _texts(MidiTextKind k) => [
        for (final t in tracks) ...t.texts.where((x) => x.kind == k)
      ]..sort((a, b) => a.tick.compareTo(b.tick));

  /// Last tick of the file: the latest note end or track end.
  int get lengthTicks {
    var end = 0;
    for (final t in tracks) {
      if (t.endTick > end) end = t.endTick;
      for (final n in t.notes) {
        if (n.endTick > end) end = n.endTick;
      }
    }
    return end;
  }

  /// Length in seconds.
  double get durationSeconds => timeMap.seconds(lengthTicks);
}

int _byStart(MidiNote a, MidiNote b) {
  final c = a.startTick.compareTo(b.startTick);
  return c != 0 ? c : a.pitch.compareTo(b.pitch);
}

class _Cursor {
  _Cursor(this.b, this.pos, this.end);
  final Uint8List b;
  int pos;
  final int end;
  bool get done => pos >= end;
  int u8() {
    if (pos >= end) throw const _Truncated();
    return b[pos++];
  }

  int vlq() {
    var v = 0;
    for (var i = 0; i < 4; i++) {
      final c = u8();
      v = (v << 7) | (c & 0x7F);
      if (c & 0x80 == 0) return v;
    }
    return v;
  }

  Uint8List take(int n) {
    if (pos + n > end) throw const _Truncated();
    final out = Uint8List.sublistView(b, pos, pos + n);
    pos += n;
    return out;
  }
}

class _Truncated implements Exception {
  const _Truncated();
}

int _u32(Uint8List b, int at) =>
    (b[at] << 24) | (b[at + 1] << 16) | (b[at + 2] << 8) | b[at + 3];
int _u16(Uint8List b, int at) => (b[at] << 8) | b[at + 1];

String _decode(Uint8List bytes) {
  try {
    return utf8.decode(bytes);
  } on FormatException {
    return latin1.decode(bytes);
  }
}

/// Reads a Standard MIDI File.
///
/// Throws [MidiFormatException] when the header is missing or malformed.
MidiFile readMidi(Uint8List bytes) {
  // A RIFF-wrapped (.rmi) file carries the SMF in its data chunk.
  var start = 0;
  if (bytes.length >= 12 &&
      String.fromCharCodes(bytes.sublist(0, 4)) == 'RIFF') {
    for (var i = 12; i + 4 <= bytes.length; i++) {
      if (bytes[i] == 0x4D && String.fromCharCodes(bytes.sublist(i, i + 4)) == 'MThd') {
        start = i;
        break;
      }
    }
  }
  if (bytes.length < start + 14 ||
      String.fromCharCodes(bytes.sublist(start, start + 4)) != 'MThd') {
    throw const MidiFormatException('no MThd header');
  }
  final hlen = _u32(bytes, start + 4);
  if (hlen < 6) throw MidiFormatException('header length $hlen');
  final format = _u16(bytes, start + 8);
  final declared = _u16(bytes, start + 10);
  final division = _u16(bytes, start + 12);
  if (format > 2) throw MidiFormatException('format $format');

  int tpq;
  double? smpte;
  if (division & 0x8000 != 0) {
    final fps = 256 - (division >> 8);
    final perFrame = division & 0xFF;
    tpq = perFrame;
    smpte = (fps == 29 ? 29.97 : fps.toDouble()) * perFrame;
  } else {
    tpq = division;
    if (tpq == 0) throw const MidiFormatException('division 0');
  }

  final warnings = <String>[];
  final tracks = <MidiTrack>[];
  final tempos = <MidiTempo>[];
  final meters = <MidiTimeSignature>[];
  final keys = <MidiKeySignature>[];

  var pos = start + 8 + hlen;
  while (pos + 8 <= bytes.length) {
    final id = String.fromCharCodes(bytes.sublist(pos, pos + 4));
    var len = _u32(bytes, pos + 4);
    pos += 8;
    if (pos + len > bytes.length) {
      if (id == 'MTrk') {
        warnings.add('track ${tracks.length} truncated');
      }
      len = bytes.length - pos;
    }
    if (id == 'MTrk') {
      tracks.add(_readTrack(
          _Cursor(bytes, pos, pos + len), tracks.length, tempos, meters, keys, warnings));
    }
    pos += len;
  }
  if (tracks.length != declared) {
    warnings.add('header declares $declared tracks, found ${tracks.length}');
  }
  tempos.sort((a, b) => a.tick.compareTo(b.tick));
  meters.sort((a, b) => a.tick.compareTo(b.tick));
  keys.sort((a, b) => a.tick.compareTo(b.tick));
  return MidiFile(
    format: format,
    ticksPerQuarter: tpq,
    smpteTicksPerSecond: smpte,
    tracks: tracks,
    tempos: tempos,
    timeSignatures: meters,
    keySignatures: keys,
    warnings: warnings,
  );
}

MidiTrack _readTrack(
  _Cursor c,
  int index,
  List<MidiTempo> tempos,
  List<MidiTimeSignature> meters,
  List<MidiKeySignature> keys,
  List<String> warnings,
) {
  final notes = <MidiNote>[];
  final controls = <MidiControlChange>[];
  final bends = <MidiPitchBend>[];
  final programs = <MidiProgramChange>[];
  final texts = <MidiText>[];
  // Open notes per channel*128+pitch, oldest first: (tick, velocity).
  final open = <int, List<(int, int)>>{};
  var tick = 0;
  var running = 0;

  void noteOff(int ch, int pitch, int vel) {
    final q = open[ch * 128 + pitch];
    if (q == null || q.isEmpty) return;
    final (at, v) = q.removeAt(0);
    notes.add(MidiNote(
        channel: ch, pitch: pitch, velocity: v, startTick: at, endTick: tick, offVelocity: vel));
  }

  try {
    while (!c.done) {
      tick += c.vlq();
      var status = c.u8();
      if (status < 0x80) {
        if (running == 0) {
          warnings.add('track $index: data byte without status at tick $tick');
          continue;
        }
        status = running;
        c.pos--;
      }
      if (status == 0xFF) {
        running = 0;
        final type = c.u8();
        final data = c.take(c.vlq());
        if (type == 0x2F) break;
        switch (type) {
          case 0x51 when data.length >= 3:
            final us = (data[0] << 16) | (data[1] << 8) | data[2];
            if (us > 0) tempos.add(MidiTempo(tick, us));
          case 0x58 when data.length >= 2:
            meters.add(MidiTimeSignature(tick, data[0], 1 << data[1],
                clocksPerClick: data.length > 2 ? data[2] : 24,
                thirtySecondsPerQuarter: data.length > 3 ? data[3] : 8));
          case 0x59 when data.length >= 2:
            keys.add(MidiKeySignature(tick, data[0].toSigned(8), minor: data[1] == 1));
          case >= 0x01 && <= 0x0F:
            final kind = type <= 0x07 ? MidiTextKind.values[type - 1] : MidiTextKind.other;
            texts.add(MidiText(tick, kind, _decode(data)));
        }
      } else if (status == 0xF0 || status == 0xF7) {
        running = 0;
        c.take(c.vlq());
      } else if (status >= 0xF0) {
        // System common/real-time bytes do not belong in a file; skip their data.
        running = 0;
        c.take(switch (status) { 0xF2 => 2, 0xF1 || 0xF3 => 1, _ => 0 });
      } else {
        running = status;
        final ch = status & 0x0F;
        final a = c.u8() & 0x7F;
        switch (status & 0xF0) {
          case 0x80:
            noteOff(ch, a, c.u8() & 0x7F);
          case 0x90:
            final v = c.u8() & 0x7F;
            if (v == 0) {
              noteOff(ch, a, 0);
            } else {
              (open[ch * 128 + a] ??= []).add((tick, v));
            }
          case 0xA0:
            c.u8();
          case 0xB0:
            controls.add(MidiControlChange(tick, ch, a, c.u8() & 0x7F));
          case 0xC0:
            programs.add(MidiProgramChange(tick, ch, a));
          case 0xD0:
            break;
          case 0xE0:
            bends.add(MidiPitchBend(tick, ch, ((c.u8() & 0x7F) << 7 | a) - 8192));
        }
      }
    }
  } on _Truncated {
    warnings.add('track $index: ends mid-event at tick $tick');
  }
  for (final e in open.entries) {
    for (final (at, v) in e.value) {
      notes.add(MidiNote(
          channel: e.key ~/ 128, pitch: e.key % 128, velocity: v, startTick: at, endTick: tick));
    }
  }
  notes.sort(_byStart);
  String? first(MidiTextKind k) {
    for (final t in texts) {
      if (t.kind == k) return t.text;
    }
    return null;
  }

  return MidiTrack(
    index: index,
    name: first(MidiTextKind.trackName),
    instrumentName: first(MidiTextKind.instrumentName),
    notes: notes,
    controls: controls,
    pitchBends: bends,
    programs: programs,
    texts: texts,
    endTick: tick,
  );
}
