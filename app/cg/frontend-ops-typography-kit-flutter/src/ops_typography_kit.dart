/// A small vocabulary for "operations console" documents: ruled section
/// labels, key-over-value stats in a divided strip, bordered status badges
/// and two-digit row indices.
///
/// Every visual choice lives in [OpsStyle]; the widgets are layout only.
/// Built on widgets.dart so consumers bring their own design system.
library;

import 'package:flutter/widgets.dart';

/// Visual settings shared by the kit. Construct once from your theme and
/// pass to each widget; defaults are neutral greys.
class OpsStyle {
  const OpsStyle({
    this.labelStyle = const TextStyle(
      fontSize: 11,
      letterSpacing: 1.6,
      fontWeight: FontWeight.w500,
      color: Color(0xFF8A8A8A),
    ),
    this.headingStyle = const TextStyle(
      fontSize: 17,
      letterSpacing: 1.8,
      fontWeight: FontWeight.w700,
      color: Color(0xFF8A8A8A),
    ),
    this.valueStyle = const TextStyle(fontSize: 16, color: Color(0xFFE6E6E6)),
    this.indexStyle = const TextStyle(fontSize: 13, color: Color(0xFF8A8A8A)),
    this.badgeStyle = const TextStyle(fontSize: 12, letterSpacing: 1),
    this.ruleColor = const Color(0xFF3A3A3A),
    this.outlineColor = const Color(0xFF6A6A6A),
    this.textColor = const Color(0xFFE6E6E6),
    this.accentColor = const Color(0xFFFF6B5B),
  });

  /// Small uppercase caption: keys over values, tags.
  final TextStyle labelStyle;

  /// Section heading over a rule.
  final TextStyle headingStyle;

  /// Values in a stat cell.
  final TextStyle valueStyle;

  /// Two-digit row indices.
  final TextStyle indexStyle;

  /// Text inside a badge; colour is supplied by the badge.
  final TextStyle badgeStyle;

  /// Hairlines: section rules, strip borders and dividers.
  final Color ruleColor;

  /// Badge border when not hot.
  final Color outlineColor;

  /// Badge text when not hot.
  final Color textColor;

  /// Highlight: accented stat values, hot badges.
  final Color accentColor;
}

/// Uppercase, letter-spaced heading with a hairline rule beneath it.
/// [trailing] sits at the right end of the heading line; with [centered]
/// the text is centred over the rule while [trailing] stays right.
class SectionLabel extends StatelessWidget {
  const SectionLabel(
    this.text, {
    super.key,
    this.style = const OpsStyle(),
    this.trailing,
    this.centered = false,
    this.padding = const EdgeInsets.only(top: 36, bottom: 8),
    this.gap = 6,
  });

  final String text;
  final OpsStyle style;
  final Widget? trailing;
  final bool centered;
  final EdgeInsets padding;

  /// Space between the heading line and the rule.
  final double gap;

  @override
  Widget build(BuildContext context) {
    final heading = Text(text.toUpperCase(), style: style.headingStyle);
    return Padding(
      padding: padding,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (centered)
            Stack(
              alignment: Alignment.bottomCenter,
              children: [
                Center(child: heading),
                if (trailing != null)
                  Align(alignment: Alignment.bottomRight, child: trailing),
              ],
            )
          else
            Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                heading,
                const Spacer(),
                if (trailing != null) trailing!,
              ],
            ),
          SizedBox(height: gap),
          Container(height: 1, color: style.ruleColor),
        ],
      ),
    );
  }
}

/// `KEY` over a value, in a fixed-height slot so neighbouring cells line
/// up whatever the value is (text, a badge, a field).
class KeyValueStat extends StatelessWidget {
  const KeyValueStat(
    this.label,
    this.value, {
    super.key,
    this.style = const OpsStyle(),
    this.accent = false,
    this.onTap,
    this.valueHeight = 36,
    this.labelGap = 4,
  });

  final String label;
  final Widget value;
  final OpsStyle style;

  /// Draw the value in [OpsStyle.accentColor].
  final bool accent;

  /// Makes the whole cell tappable.
  final VoidCallback? onTap;

  /// Height of the value slot; the value is aligned centre-left inside it.
  final double valueHeight;

  final double labelGap;

  @override
  Widget build(BuildContext context) {
    final body = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(label.toUpperCase(), style: style.labelStyle),
        SizedBox(height: labelGap),
        SizedBox(
          height: valueHeight,
          child: Align(
            alignment: Alignment.centerLeft,
            child: DefaultTextStyle(
              style: accent
                  ? style.valueStyle.copyWith(color: style.accentColor)
                  : style.valueStyle,
              child: value,
            ),
          ),
        ),
      ],
    );
    if (onTap == null) return body;
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: onTap,
        child: body,
      ),
    );
  }
}

/// Horizontal strip of cells with hairline dividers between them, ruled
/// top and bottom. [trailing] is anchored to the right edge. When the
/// cells don't fit beside [trailing] they wrap onto further lines rather
/// than overflow. An optional [header] line sits inside the top rule,
/// above the cells only — [trailing] keeps its place beside them.
class StatStrip extends StatelessWidget {
  const StatStrip({
    super.key,
    required this.children,
    this.trailing,
    this.style = const OpsStyle(),
    this.padding = const EdgeInsets.symmetric(horizontal: 8, vertical: 10),
    this.dividerHeight = 44,
    this.dividerMargin = const EdgeInsets.symmetric(horizontal: 20),
    this.runSpacing = 12,
    this.header,
    this.headerGap = 10,
  });

  final List<Widget> children;
  final Widget? trailing;

  /// A line above the cells, inside the strip's rules.
  final Widget? header;
  final double headerGap;
  final OpsStyle style;
  final EdgeInsets padding;
  final double dividerHeight;
  final EdgeInsets dividerMargin;

  /// Vertical gap between wrapped lines of cells.
  final double runSpacing;

  @override
  Widget build(BuildContext context) {
    Widget divider() => Container(
          width: 1,
          height: dividerHeight,
          margin: dividerMargin,
          color: style.ruleColor,
        );
    // Each cell carries its own leading divider so a wrapped line still
    // reads as a sequence of cells.
    final cells = Wrap(
      runSpacing: runSpacing,
      crossAxisAlignment: WrapCrossAlignment.end,
      children: [
        for (var i = 0; i < children.length; i++)
          Row(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [if (i > 0) divider(), children[i]],
          ),
      ],
    );
    final lead = header == null
        ? cells
        : Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [header!, SizedBox(height: headerGap), cells],
          );
    return Container(
      padding: padding,
      decoration: BoxDecoration(
        border: Border.symmetric(horizontal: BorderSide(color: style.ruleColor)),
      ),
      child: trailing == null
          ? lead
          : Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Expanded(child: lead),
                const SizedBox(width: 16),
                trailing!,
              ],
            ),
    );
  }
}

/// Small bordered uppercase badge. [hot] draws it in the accent colour.
/// Pure display; wrap it in your own menu or button to make it act.
class StatusBadge extends StatelessWidget {
  const StatusBadge(
    this.value, {
    super.key,
    this.style = const OpsStyle(),
    this.hot = false,
    this.padding = const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
    this.borderRadius = 3,
  });

  final String value;
  final OpsStyle style;
  final bool hot;
  final EdgeInsets padding;
  final double borderRadius;

  @override
  Widget build(BuildContext context) {
    final color = hot ? style.accentColor : style.textColor;
    return Container(
      padding: padding,
      decoration: BoxDecoration(
        border: Border.all(color: hot ? style.accentColor : style.outlineColor),
        borderRadius: BorderRadius.circular(borderRadius),
      ),
      child: Text(value.toUpperCase(), style: style.badgeStyle.copyWith(color: color)),
    );
  }
}

/// Zero-padded row number: 01, 02 … in a fixed-width slot.
class RowIndex extends StatelessWidget {
  const RowIndex(
    this.index, {
    super.key,
    this.style = const OpsStyle(),
    this.width = 34,
    this.digits = 2,
    this.oneBased = true,
  });

  /// Zero-based position in the list.
  final int index;
  final OpsStyle style;
  final double width;
  final int digits;

  /// Show `index + 1`, so the first row reads 01.
  final bool oneBased;

  @override
  Widget build(BuildContext context) {
    final n = oneBased ? index + 1 : index;
    return SizedBox(
      width: width,
      child: Text(n.toString().padLeft(digits, '0'), style: style.indexStyle),
    );
  }
}
