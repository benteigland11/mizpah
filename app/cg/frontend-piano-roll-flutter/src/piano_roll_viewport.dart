import 'package:flutter/foundation.dart';

/// The visible window of a piano roll: a time range and a pitch range.
///
/// Time is in whatever unit the caller's notes use (ticks, seconds, beats).
/// Pitch is continuous so the window can scroll smoothly; row `p` spans
/// `[p, p + 1)`. Other views (lanes, rulers) listen to the same viewport to
/// scroll with the roll. The window never leaves its bounds.
class PianoRollViewport extends ChangeNotifier {
  /// Creates a viewport over `[timeMin, timeMax]` and pitches
  /// `[pitchMin, pitchMax)`, initially showing all of it.
  PianoRollViewport({
    double timeMin = 0,
    double timeMax = 1,
    double pitchMin = 0,
    double pitchMax = 128,
    this.minTimeSpan = 0,
    this.minPitchSpan = 6,
  }) {
    setBounds(timeMin: timeMin, timeMax: timeMax, pitchMin: pitchMin, pitchMax: pitchMax);
  }

  /// Narrowest time window zoom may reach (0: a thousandth of the bounds).
  final double minTimeSpan;

  /// Fewest pitch rows zoom may reach.
  final double minPitchSpan;

  double _timeMin = 0, _timeMax = 1, _pitchMin = 0, _pitchMax = 128;
  double _start = 0, _span = 1, _low = 0, _high = 128;

  /// Earliest time the window can show.
  double get timeMin => _timeMin;

  /// Latest time the window can show.
  double get timeMax => _timeMax;

  /// Lowest pitch row the window can show.
  double get pitchMin => _pitchMin;

  /// Top edge of the highest pitch row the window can show.
  double get pitchMax => _pitchMax;

  /// Time at the left edge.
  double get start => _start;

  /// Width of the window in time.
  double get span => _span;

  /// Time at the right edge.
  double get end => _start + _span;

  /// Pitch at the bottom edge.
  double get pitchLow => _low;

  /// Pitch at the top edge.
  double get pitchHigh => _high;

  double get _minSpan =>
      minTimeSpan > 0 ? minTimeSpan : (_timeMax - _timeMin) / 1000;

  /// Sets the scrollable extent and shows all of it.
  void setBounds({
    required double timeMin,
    required double timeMax,
    double pitchMin = 0,
    double pitchMax = 128,
  }) {
    _timeMin = timeMin;
    _timeMax = timeMax > timeMin ? timeMax : timeMin + 1;
    _pitchMin = pitchMin.clamp(0, 127).toDouble();
    _pitchMax = pitchMax.clamp(_pitchMin + 1, 128).toDouble();
    _start = _timeMin;
    _span = _timeMax - _timeMin;
    _low = _pitchMin;
    _high = _pitchMax;
    _fixPitch();
    notifyListeners();
  }

  /// Shows the whole time range and all pitches in bounds.
  void fit() => setBounds(
      timeMin: _timeMin, timeMax: _timeMax, pitchMin: _pitchMin, pitchMax: _pitchMax);

  /// Shows `[start, start + span]`, clamped to the bounds.
  void setTime(double start, double span) {
    final total = _timeMax - _timeMin;
    _span = span.clamp(_minSpan, total).toDouble();
    _start = start.clamp(_timeMin, _timeMax - _span).toDouble();
    notifyListeners();
  }

  /// Shows pitches `[low, high)`, clamped to the bounds.
  void setPitch(double low, double high) {
    final total = _pitchMax - _pitchMin;
    final span = (high - low).clamp(minPitchSpan.clamp(1, total), total).toDouble();
    _low = low.clamp(_pitchMin, _pitchMax - span).toDouble();
    _high = _low + span;
    notifyListeners();
  }

  void _fixPitch() {
    final total = _pitchMax - _pitchMin;
    final span = (_high - _low).clamp(minPitchSpan.clamp(1, total), total).toDouble();
    _low = _low.clamp(_pitchMin, _pitchMax - span).toDouble();
    _high = _low + span;
  }

  /// Moves the window by [dt] in time.
  void panTime(double dt) => setTime(_start + dt, _span);

  /// Moves the window by [dp] pitches (positive is up).
  void panPitch(double dp) => setPitch(_low + dp, _high + dp);

  /// Scales the time window by [factor] (< 1 zooms in) keeping [anchor]
  /// (a time, default the centre) at the same screen position.
  void zoomTime(double factor, {double? anchor}) {
    final a = anchor ?? _start + _span / 2;
    final f = (a - _start) / _span;
    final span = (_span * factor).clamp(_minSpan, _timeMax - _timeMin).toDouble();
    setTime(a - f * span, span);
  }

  /// Scales the pitch window by [factor] (< 1 zooms in) around [anchor].
  void zoomPitch(double factor, {double? anchor}) {
    final a = anchor ?? (_low + _high) / 2;
    final f = (a - _low) / (_high - _low);
    final span = (_high - _low) * factor;
    setPitch(a - f * span, a - f * span + span);
  }
}
