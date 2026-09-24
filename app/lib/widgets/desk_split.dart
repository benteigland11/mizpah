import 'package:flutter/material.dart';

import '../theme/app_theme.dart';
import '../theme/kit_styles.dart';

/// A list of documents beside the one that is open. The paper is the
/// thing being read, so it is laid out first, at its optimal width; the
/// list takes what is left, narrowing from [listWidth] down to [listMin],
/// and when even that would squeeze the paper (an iPad, a small window)
/// it folds to a rail while a document is open. A tap on the rail brings
/// the list back over the paper.
class DeskSplit extends StatefulWidget {
  const DeskSplit({
    super.key,
    required this.list,
    required this.sheet,
    required this.hasDocument,
    this.listWidth = 400,
    this.listMin = 236,
    this.sheetWidth = 820,
    this.listLabel = 'LIST',
    this.count,
    this.selection,
  });

  /// What is open on the right, by identity. When it changes while the
  /// list is peeked over the paper, the person picked something: the
  /// peek closes. Nothing else closes it but the rail or the dim.
  final Object? selection;

  final Widget list;
  final Widget sheet;

  /// Whether a document is open on the right; with none, the list has the
  /// room to itself.
  final bool hasDocument;
  final double listWidth;
  final double listMin;

  /// The paper's own width; padding around it comes on top.
  final double sheetWidth;
  final String listLabel;

  /// A count for the rail (unread, open).
  final int? count;

  /// Padding around the paper for a pane this wide: generous where there
  /// is room, tight where there is not, so the paper itself keeps its
  /// width as long as it can.
  static EdgeInsets paddingFor(double paneWidth, double sheetWidth) {
    final spare = paneWidth - sheetWidth;
    final side = spare >= 80 ? 40.0 : (spare / 2).clamp(6.0, 40.0);
    return EdgeInsets.fromLTRB(side, 28, side, 48);
  }

  @override
  State<DeskSplit> createState() => _DeskSplitState();
}

class _DeskSplitState extends State<DeskSplit> {
  /// The list, shown over the paper while the rail is in use.
  bool peek = false;

  @override
  void didUpdateWidget(covariant DeskSplit old) {
    super.didUpdateWidget(old);
    if (peek && old.selection != widget.selection) peek = false;
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return LayoutBuilder(
      builder: (context, c) {
        final w = c.maxWidth;
        // What the paper needs with the tightest padding it accepts.
        final sheetNeed = widget.sheetWidth + 12;
        if (!widget.hasDocument) {
          // Nothing open: the list keeps the width it has beside a sheet, so
          // its header and rows never move when one opens or closes.
          return _row(context, listWidth: widget.listWidth.clamp(0, w), rail: false);
        }
        final room = w - sheetNeed;
        if (room >= widget.listMin) {
          peek = false;
          return _row(context, listWidth: room.clamp(widget.listMin, widget.listWidth), rail: false);
        }
        // Not enough for both: the paper wins; the list folds to a rail.
        return Stack(
          children: [
            _row(context, listWidth: _railWidth, rail: true),
            if (peek)
              Positioned.fill(
                child: GestureDetector(
                  behavior: HitTestBehavior.opaque,
                  onTap: () => setState(() => peek = false),
                  child: ColoredBox(color: Colors.black.withValues(alpha: 0.35)),
                ),
              ),
            if (peek)
              Positioned(
                left: _railWidth,
                top: 0,
                bottom: 0,
                width: widget.listWidth.clamp(0, w - _railWidth),
                child: Material(
                  elevation: 12,
                  color: cs.surface,
                  child: widget.list,
                ),
              ),
          ],
        );
      },
    );
  }

  static const _railWidth = 44.0;

  Widget _row(BuildContext context, {required double listWidth, required bool rail}) {
    final cs = Theme.of(context).colorScheme;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SizedBox(width: listWidth, child: rail ? _rail(context) : widget.list),
        VerticalDivider(width: 1, color: cs.outlineVariant),
        Expanded(child: widget.sheet),
      ],
    );
  }

  /// The folded list: its name down the edge and the count, tap to peek.
  Widget _rail(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final ink = AppTheme.ink(context);
    return InkWell(
      onTap: () => setState(() => peek = !peek),
      child: Container(
        color: cs.surfaceContainerLow,
        padding: const EdgeInsets.symmetric(vertical: Sp.m),
        child: Column(
          children: [
            Icon(Icons.chevron_right, size: 18, color: cs.onSurfaceVariant),
            const SizedBox(height: Sp.s),
            if (widget.count != null && widget.count! > 0)
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(color: cs.primary, borderRadius: BorderRadius.circular(AppTheme.radius)),
                child: Text('${widget.count}', style: AppTheme.mono.copyWith(fontSize: 11, fontWeight: FontWeight.w700, color: cs.onPrimary)),
              ),
            const SizedBox(height: Sp.m),
            Expanded(
              child: RotatedBox(
                quarterTurns: 1,
                child: Text(widget.listLabel, style: opsLabelStyle(context).copyWith(color: ink)),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
