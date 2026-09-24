/// Lines of a redline: unchanged context, additions and removals, each
/// with a gutter mark, optional zero-padded index and optional trailing
/// badge text. Built on widgets.dart; every colour and style is a
/// parameter of [DiffStyle].
library;

import 'package:flutter/widgets.dart';

/// What a line is in the diff.
enum DiffKind { context, added, removed }

/// Colours and text styles for [DiffLine]. Defaults: green additions, red
/// removals, dim context on a dark surface.
class DiffStyle {
  const DiffStyle({
    this.textStyle = const TextStyle(fontSize: 16, color: Color(0xFFE6E6E6)),
    this.contextColor = const Color(0xFF8A8A8A),
    this.addedColor = const Color(0xFF5FD38D),
    this.removedColor = const Color(0xFFF07178),
    this.tintAlpha = 0.10,
    this.markStyle = const TextStyle(fontSize: 15, fontWeight: FontWeight.w700),
    this.indexStyle = const TextStyle(fontSize: 13, color: Color(0xFF8A8A8A)),
    this.trailingStyle = const TextStyle(fontSize: 11, letterSpacing: 1),
    this.addedMark = '+',
    this.removedMark = '−',
    this.padding = const EdgeInsets.symmetric(vertical: 6, horizontal: 8),
    this.markWidth = 20,
    this.indexWidth = 34,
  });

  /// Base style for line text. Context lines take [contextColor].
  final TextStyle textStyle;
  final Color contextColor;
  final Color addedColor;
  final Color removedColor;

  /// Opacity of the background tint on added/removed lines.
  final double tintAlpha;

  /// Style for the gutter mark; colour comes from the line kind.
  final TextStyle markStyle;
  final TextStyle indexStyle;

  /// Style for trailing badge text; colour comes from the line kind.
  final TextStyle trailingStyle;
  final String addedMark;
  final String removedMark;
  final EdgeInsets padding;
  final double markWidth;
  final double indexWidth;
}

/// One line of a diff.
class DiffLine extends StatelessWidget {
  const DiffLine(
    this.text, {
    super.key,
    required this.kind,
    this.style = const DiffStyle(),
    this.index,
    this.trailing,
    this.indexDigits = 2,
  });

  const DiffLine.context(String text,
      {Key? key, DiffStyle style = const DiffStyle(), int? index})
      : this(text, key: key, kind: DiffKind.context, style: style, index: index);

  const DiffLine.added(String text,
      {Key? key, DiffStyle style = const DiffStyle(), int? index, String? trailing})
      : this(text, key: key, kind: DiffKind.added, style: style, index: index,
            trailing: trailing);

  const DiffLine.removed(String text,
      {Key? key, DiffStyle style = const DiffStyle(), int? index})
      : this(text, key: key, kind: DiffKind.removed, style: style, index: index);

  final String text;
  final DiffKind kind;
  final DiffStyle style;

  /// Zero-based position; rendered one-based and zero-padded (01, 02 …).
  final int? index;

  /// Short badge text at the right end, in the line's colour.
  final String? trailing;
  final int indexDigits;

  Color get _color => switch (kind) {
        DiffKind.context => style.contextColor,
        DiffKind.added => style.addedColor,
        DiffKind.removed => style.removedColor,
      };

  String get _mark => switch (kind) {
        DiffKind.context => '',
        DiffKind.added => style.addedMark,
        DiffKind.removed => style.removedMark,
      };

  @override
  Widget build(BuildContext context) {
    final color = _color;
    final tint = kind == DiffKind.context
        ? null
        : color.withValues(alpha: style.tintAlpha);
    final textStyle = style.textStyle.copyWith(
      color: kind == DiffKind.context ? style.contextColor : null,
      decoration: kind == DiffKind.removed ? TextDecoration.lineThrough : null,
      decorationColor: color,
    );
    return Container(
      padding: style.padding,
      color: tint,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: style.markWidth,
            child: Text(_mark, style: style.markStyle.copyWith(color: color)),
          ),
          if (index != null)
            SizedBox(
              width: style.indexWidth,
              child: Text((index! + 1).toString().padLeft(indexDigits, '0'),
                  style: style.indexStyle),
            ),
          Expanded(child: Text(text, style: textStyle)),
          if (trailing != null)
            Text(trailing!, style: style.trailingStyle.copyWith(color: color)),
        ],
      ),
    );
  }
}

/// A section's existing lines as context, followed by one added line.
/// The common "insert at the end of a list" redline.
class AppendDiff extends StatelessWidget {
  const AppendDiff({
    super.key,
    required this.existing,
    required this.added,
    this.style = const DiffStyle(),
    this.trailing,
    this.indexed = true,
  });

  final List<String> existing;
  final String added;
  final DiffStyle style;
  final String? trailing;

  /// Number the lines 01, 02 …
  final bool indexed;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (var i = 0; i < existing.length; i++)
          DiffLine.context(existing[i], style: style, index: indexed ? i : null),
        DiffLine.added(added,
            style: style,
            index: indexed ? existing.length : null,
            trailing: trailing),
      ],
    );
  }
}

/// One line replaced by another: the old struck through, the new beneath.
class ReplaceDiff extends StatelessWidget {
  const ReplaceDiff({
    super.key,
    required this.before,
    required this.after,
    this.style = const DiffStyle(),
  });

  final String before;
  final String after;
  final DiffStyle style;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        DiffLine.removed(before, style: style),
        DiffLine.added(after, style: style),
      ],
    );
  }
}
