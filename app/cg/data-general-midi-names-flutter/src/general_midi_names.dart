/// General MIDI naming: instruments, percussion, pitches and keys.
///
/// ```dart
/// gmProgram(40).name;          // 'Violin'
/// gmProgram(40).family;        // GmFamily.strings
/// gmDrum(38);                  // 'Acoustic Snare'
/// pitchName(61, keySharps: -3) // 'D♭4'
/// keyName(-3, minor: true)     // 'C minor'
/// ```
///
/// Program numbers are 0-based (0 is Acoustic Grand Piano), as they appear
/// in a program-change message. Middle C (60) is C4 unless the caller picks
/// another octave convention.
library;

/// The sixteen General MIDI instrument families, eight programs each.
enum GmFamily {
  /// Programs 0-7.
  piano('Piano'),

  /// 8-15.
  chromaticPercussion('Chromatic Percussion'),

  /// 16-23.
  organ('Organ'),

  /// 24-31.
  guitar('Guitar'),

  /// 32-39.
  bass('Bass'),

  /// 40-47.
  strings('Strings'),

  /// 48-55.
  ensemble('Ensemble'),

  /// 56-63.
  brass('Brass'),

  /// 64-71.
  reed('Reed'),

  /// 72-79.
  pipe('Pipe'),

  /// 80-87.
  synthLead('Synth Lead'),

  /// 88-95.
  synthPad('Synth Pad'),

  /// 96-103.
  synthEffects('Synth Effects'),

  /// 104-111.
  ethnic('Ethnic'),

  /// 112-119.
  percussive('Percussive'),

  /// 120-127.
  soundEffects('Sound Effects');

  const GmFamily(this.label);

  /// Display name of the family.
  final String label;
}

/// A General MIDI program.
class GmInstrument {
  /// Creates an instrument entry.
  const GmInstrument(this.program, this.name, this.family);

  /// Program number 0-127.
  final int program;

  /// Instrument name, e.g. 'Violin'.
  final String name;

  /// Family the program belongs to.
  final GmFamily family;

  @override
  String toString() => name;
}

const List<String> _programs = [
  'Acoustic Grand Piano', 'Bright Acoustic Piano', 'Electric Grand Piano',
  'Honky-tonk Piano', 'Electric Piano 1', 'Electric Piano 2', 'Harpsichord',
  'Clavinet',
  'Celesta', 'Glockenspiel', 'Music Box', 'Vibraphone', 'Marimba',
  'Xylophone', 'Tubular Bells', 'Dulcimer',
  'Drawbar Organ', 'Percussive Organ', 'Rock Organ', 'Church Organ',
  'Reed Organ', 'Accordion', 'Harmonica', 'Tango Accordion',
  'Acoustic Guitar (nylon)', 'Acoustic Guitar (steel)',
  'Electric Guitar (jazz)', 'Electric Guitar (clean)',
  'Electric Guitar (muted)', 'Overdriven Guitar', 'Distortion Guitar',
  'Guitar Harmonics',
  'Acoustic Bass', 'Electric Bass (finger)', 'Electric Bass (pick)',
  'Fretless Bass', 'Slap Bass 1', 'Slap Bass 2', 'Synth Bass 1',
  'Synth Bass 2',
  'Violin', 'Viola', 'Cello', 'Contrabass', 'Tremolo Strings',
  'Pizzicato Strings', 'Orchestral Harp', 'Timpani',
  'String Ensemble 1', 'String Ensemble 2', 'Synth Strings 1',
  'Synth Strings 2', 'Choir Aahs', 'Voice Oohs', 'Synth Voice',
  'Orchestra Hit',
  'Trumpet', 'Trombone', 'Tuba', 'Muted Trumpet', 'French Horn',
  'Brass Section', 'Synth Brass 1', 'Synth Brass 2',
  'Soprano Sax', 'Alto Sax', 'Tenor Sax', 'Baritone Sax', 'Oboe',
  'English Horn', 'Bassoon', 'Clarinet',
  'Piccolo', 'Flute', 'Recorder', 'Pan Flute', 'Blown Bottle',
  'Shakuhachi', 'Whistle', 'Ocarina',
  'Lead 1 (square)', 'Lead 2 (sawtooth)', 'Lead 3 (calliope)',
  'Lead 4 (chiff)', 'Lead 5 (charang)', 'Lead 6 (voice)',
  'Lead 7 (fifths)', 'Lead 8 (bass + lead)',
  'Pad 1 (new age)', 'Pad 2 (warm)', 'Pad 3 (polysynth)', 'Pad 4 (choir)',
  'Pad 5 (bowed)', 'Pad 6 (metallic)', 'Pad 7 (halo)', 'Pad 8 (sweep)',
  'FX 1 (rain)', 'FX 2 (soundtrack)', 'FX 3 (crystal)',
  'FX 4 (atmosphere)', 'FX 5 (brightness)', 'FX 6 (goblins)',
  'FX 7 (echoes)', 'FX 8 (sci-fi)',
  'Sitar', 'Banjo', 'Shamisen', 'Koto', 'Kalimba', 'Bagpipe', 'Fiddle',
  'Shanai',
  'Tinkle Bell', 'Agogo', 'Steel Drums', 'Woodblock', 'Taiko Drum',
  'Melodic Tom', 'Synth Drum', 'Reverse Cymbal',
  'Guitar Fret Noise', 'Breath Noise', 'Seashore', 'Bird Tweet',
  'Telephone Ring', 'Helicopter', 'Applause', 'Gunshot',
];

/// The General MIDI instrument for [program] (0-127, clamped).
GmInstrument gmProgram(int program) {
  final p = program.clamp(0, 127);
  return GmInstrument(p, _programs[p], GmFamily.values[p ~/ 8]);
}

/// All 128 General MIDI instruments in program order.
List<GmInstrument> get gmInstruments =>
    [for (var p = 0; p < 128; p++) gmProgram(p)];

// GM Level 1 (35-81) with the GM2 additions below and above (27-34, 82-87).
const Map<int, String> _drums = {
  27: 'High Q', 28: 'Slap', 29: 'Scratch Push', 30: 'Scratch Pull',
  31: 'Sticks', 32: 'Square Click', 33: 'Metronome Click',
  34: 'Metronome Bell',
  35: 'Acoustic Bass Drum', 36: 'Kick', 37: 'Side Stick',
  38: 'Acoustic Snare', 39: 'Hand Clap', 40: 'Electric Snare',
  41: 'Low Floor Tom', 42: 'Closed Hi-Hat', 43: 'High Floor Tom',
  44: 'Pedal Hi-Hat', 45: 'Low Tom', 46: 'Open Hi-Hat', 47: 'Low-Mid Tom',
  48: 'Hi-Mid Tom', 49: 'Crash Cymbal 1', 50: 'High Tom',
  51: 'Ride Cymbal 1', 52: 'Chinese Cymbal', 53: 'Ride Bell',
  54: 'Tambourine', 55: 'Splash Cymbal', 56: 'Cowbell',
  57: 'Crash Cymbal 2', 58: 'Vibraslap', 59: 'Ride Cymbal 2',
  60: 'Hi Bongo', 61: 'Low Bongo', 62: 'Mute Hi Conga',
  63: 'Open Hi Conga', 64: 'Low Conga', 65: 'High Timbale',
  66: 'Low Timbale', 67: 'High Agogo', 68: 'Low Agogo', 69: 'Cabasa',
  70: 'Maracas', 71: 'Short Whistle', 72: 'Long Whistle', 73: 'Short Guiro',
  74: 'Long Guiro', 75: 'Claves', 76: 'Hi Wood Block', 77: 'Low Wood Block',
  78: 'Mute Cuica', 79: 'Open Cuica', 80: 'Mute Triangle',
  81: 'Open Triangle',
  82: 'Shaker', 83: 'Jingle Bell', 84: 'Bell Tree', 85: 'Castanets',
  86: 'Mute Surdo', 87: 'Open Surdo',
};

/// The kit piece for a percussion-channel [note], or null outside the map.
String? gmDrum(int note) => _drums[note];

/// Whether [channel] (0-based) is the General MIDI percussion channel.
bool isGmPercussionChannel(int channel) => channel == 9;

const List<String> _sharpNames = [
  'C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B'
];
const List<String> _flatNames = [
  'C', 'D♭', 'D', 'E♭', 'E', 'F', 'G♭', 'G', 'A♭', 'A', 'B♭', 'B'
];

/// Name of MIDI [note] with its octave, e.g. 'C4' for 60.
///
/// Black keys are spelled with flats when [keySharps] is negative (a flat
/// key) and with sharps otherwise. [ascii] writes '#' and 'b' instead of
/// '♯' and '♭'. [middleCOctave] sets the octave number of note 60.
String pitchName(int note,
    {int? keySharps, bool ascii = false, int middleCOctave = 4}) {
  final octave = note ~/ 12 - 5 + middleCOctave;
  return '${pitchClassName(note, keySharps: keySharps, ascii: ascii)}$octave';
}

/// Name of MIDI [note] without its octave, spelled as in [pitchName].
String pitchClassName(int note, {int? keySharps, bool ascii = false}) {
  final names = (keySharps ?? 0) < 0 ? _flatNames : _sharpNames;
  final n = names[note % 12];
  return ascii ? n.replaceAll('♯', '#').replaceAll('♭', 'b') : n;
}

/// Whether MIDI [note] is a black key on a piano.
bool isBlackKey(int note) => const {1, 3, 6, 8, 10}.contains(note % 12);

const List<String> _majorKeys = [
  'C♭', 'G♭', 'D♭', 'A♭', 'E♭', 'B♭', 'F', 'C', 'G', 'D', 'A', 'E', 'B',
  'F♯', 'C♯'
];
const List<String> _minorKeys = [
  'A♭', 'E♭', 'B♭', 'F', 'C', 'G', 'D', 'A', 'E', 'B', 'F♯', 'C♯', 'G♯',
  'D♯', 'A♯'
];

/// Name of the key with [sharps] sharps (negative for flats), e.g. 'E♭ major'.
///
/// [sharps] is clamped to -7..7.
String keyName(int sharps, {bool minor = false, bool ascii = false}) {
  final i = sharps.clamp(-7, 7) + 7;
  final k = '${(minor ? _minorKeys : _majorKeys)[i]} ${minor ? 'minor' : 'major'}';
  return ascii ? k.replaceAll('♯', '#').replaceAll('♭', 'b') : k;
}
