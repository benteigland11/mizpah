/// A thin horizontal bar showing how much of a capacity is taken, split
/// into coloured segments (done / reserved / …) with the remainder empty.
/// When the segments exceed the capacity the bar is drawn full and its
/// outline switches to the overrun colour.
library;

import 'package:flutter/widgets.dart';

/// One coloured portion of the bar.
class CapacitySegment {
  const CapacitySegment(this.value, this.color);

  /// Amount in the same unit as the capacity. Zero segments are skipped.
  final num value;
  final Color color;
}

/// Summary of a bar's numbers, for the caller's readout.
class CapacityReadout {
  const CapacityReadout({
    required this.used,
    required this.capacity,
  });
  final num used;

  /// Null when no capacity is set; the bar then scales to [used].
  final num? capacity;

  num? get remaining => capacity == null ? null : capacity! - used;
  bool get over => capacity != null && used > capacity!;
  num get overBy => over ? used - capacity! : 0;

  static CapacityReadout of(List<CapacitySegment> segments, num? capacity) =>
      CapacityReadout(
        used: segments.fold<num>(0, (a, s) => a + s.value),
        capacity: capacity,
      );
}

/// The bar. Segments fill left to right in order, proportional to
/// [capacity] (or to their sum when no capacity is given).
class SegmentedCapacityBar extends StatelessWidget {
  const SegmentedCapacityBar({
    super.key,
    required this.segments,
    this.capacity,
    this.height = 8,
    this.borderRadius = 2,
    this.outlineColor = const Color(0xFF6A6A6A),
    this.overrunColor = const Color(0xFFF5A524),
    this.borderWidth = 1,
  });

  final List<CapacitySegment> segments;

  /// Total the bar represents. Null: the bar scales to the segments' sum.
  final num? capacity;
  final double height;
  final double borderRadius;
  final Color outlineColor;

  /// Outline colour when the segments exceed [capacity].
  final Color overrunColor;
  final double borderWidth;

  CapacityReadout get readout => CapacityReadout.of(segments, capacity);

  @override
  Widget build(BuildContext context) {
    final r = readout;
    final total = capacity ?? r.used;
    final remaining = r.remaining == null ? 0 : r.remaining!.clamp(0, total);

    // Flex wants ints; scale to parts-per-million of the total. A zero-flex
    // Expanded stops flexing, so zero-value segments are skipped entirely.
    int flexOf(num v) =>
        total <= 0 ? 0 : (v / total * 1000000).round().clamp(1, 1000000);

    return Container(
      height: height,
      decoration: BoxDecoration(
        border: Border.all(
          color: r.over ? overrunColor : outlineColor,
          width: borderWidth,
        ),
        borderRadius: BorderRadius.circular(borderRadius),
      ),
      clipBehavior: Clip.antiAlias,
      child: Row(
        children: [
          for (final s in segments)
            if (s.value > 0)
              Expanded(flex: flexOf(s.value), child: ColoredBox(color: s.color)),
          if (remaining > 0)
            Expanded(flex: flexOf(remaining), child: const SizedBox.shrink()),
        ],
      ),
    );
  }
}
