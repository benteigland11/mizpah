import 'package:flutter/material.dart';

import '../cg/ops_typography_kit/ops_typography_kit.dart';
import '../theme/app_theme.dart';
import '../theme/kit_styles.dart';

/// The console vocabulary from the ops-typography-kit widget, dressed in
/// the app theme, plus the few app-level pieces built on it (sheet,
/// remove, add). Screens use these names; the kit stays theme-free.

class OpsLabel extends StatelessWidget {
  const OpsLabel(this.text, {super.key, this.trailing, this.centered = true});
  final String text;
  final Widget? trailing;
  final bool centered;

  @override
  Widget build(BuildContext context) => SectionLabel(
    text,
    style: opsStyle(context),
    trailing: trailing,
    centered: centered,
  );
}

class OpsStat extends StatelessWidget {
  const OpsStat(
    this.label,
    this.value, {
    super.key,
    this.accent = false,
    this.onTap,
  });
  final String label;
  final Widget value;
  final bool accent;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) => KeyValueStat(
    label,
    value,
    style: opsStyle(context),
    accent: accent,
    onTap: onTap,
  );
}

class OpsStrip extends StatelessWidget {
  const OpsStrip({
    super.key,
    required this.children,
    this.trailing,
    this.header,
  });
  final List<Widget> children;
  final Widget? trailing;
  final Widget? header;

  @override
  Widget build(BuildContext context) => StatStrip(
    style: opsStyle(context),
    trailing: trailing,
    header: header,
    headerGap: 22,
    children: children,
  );
}

class OpsIndex extends StatelessWidget {
  const OpsIndex(this.i, {super.key});
  final int i;
  @override
  Widget build(BuildContext context) => RowIndex(i, style: opsStyle(context));
}

/// Status badge; with [options] it opens a menu on tap.
class OpsChip extends StatelessWidget {
  const OpsChip({
    super.key,
    required this.value,
    required this.options,
    required this.onSelected,
    this.hot = const {},
  });
  final String value;
  final List<String> options;
  final ValueChanged<String> onSelected;

  /// Values drawn in the accent colour.
  final Set<String> hot;

  @override
  Widget build(BuildContext context) {
    final badge = StatusBadge(
      value,
      style: opsStyle(context),
      hot: hot.contains(value),
    );
    if (options.isEmpty) return badge;
    return PopupMenuButton<String>(
      tooltip: '',
      onSelected: onSelected,
      itemBuilder: (_) => [
        for (final o in options)
          PopupMenuItem(
            value: o,
            child: Text(
              o.toUpperCase(),
              style: AppTheme.mono.copyWith(fontSize: 12),
            ),
          ),
      ],
      child: badge,
    );
  }
}

/// Remove is always ×: permanent, muted, compact.
/// A failed action, said once, the same way everywhere: a small mark, one
/// line in the error colour, selectable so it can be copied into a report.
/// Every screen wrote its own before — red body text here, a key label
/// there, a snackbar somewhere else.
class OpsError extends StatelessWidget {
  const OpsError(this.text, {super.key});
  final String text;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Semantics(
      liveRegion: true,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Icon(Icons.error_outline, size: 14, color: cs.error),
          ),
          const SizedBox(width: 6),
          Expanded(
            child: SelectableText(
              text,
              style: AppTheme.mono.copyWith(fontSize: 12, height: 1.4, color: cs.error),
            ),
          ),
        ],
      ),
    );
  }
}

class OpsRemove extends StatelessWidget {
  const OpsRemove({
    super.key,
    required this.onPressed,
    this.tooltip = 'Remove',
  });
  final VoidCallback onPressed;
  final String tooltip;

  @override
  // The glyph stays small; the hit area is what a finger needs. The
  // button is drawn 36 wide but laid out on a 24 line: it overhangs its
  // neighbours' padding rather than pushing rows apart.
  Widget build(BuildContext context) => SizedBox(
    width: 24,
    height: 24,
    child: OverflowBox(
      maxWidth: 36,
      maxHeight: 36,
      child: IconButton(
        icon: const Icon(Icons.close, size: 14),
        visualDensity: VisualDensity.compact,
        padding: EdgeInsets.zero,
        constraints: const BoxConstraints.tightFor(width: 36, height: 36),
        color: Theme.of(context).colorScheme.outline,
        tooltip: tooltip,
        onPressed: onPressed,
      ),
    ),
  );
}

/// Add is always +: a muted line at the foot of a section.
class OpsAddLine extends StatelessWidget {
  const OpsAddLine({super.key, required this.label, required this.onTap});
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(6, 8, 6, 6),
        child: Row(
          children: [
            Icon(Icons.add, size: 14, color: cs.onSurfaceVariant),
            const SizedBox(width: 6),
            Text(
              label.toUpperCase(),
              style: opsLabelStyle(context).copyWith(letterSpacing: 1.2),
            ),
          ],
        ),
      ),
    );
  }
}

/// A sheet on the desk: bordered panel on a lighter surface. Used for the
/// memo page and for dialogs, so they read as the same kind of paper.
class OpsSheet extends StatelessWidget {
  const OpsSheet({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.fromLTRB(64, 56, 64, 56),
    this.maxWidth,
  });
  final Widget child;
  final EdgeInsets padding;
  final double? maxWidth;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final sheet = Container(
      padding: padding,
      decoration: BoxDecoration(
        color: cs.surfaceContainer,
        border: Border.all(color: cs.outline),
        borderRadius: BorderRadius.circular(4),
      ),
      child: child,
    );
    if (maxWidth == null) return sheet;
    return ConstrainedBox(
      constraints: BoxConstraints(maxWidth: maxWidth!),
      child: sheet,
    );
  }
}

/// A dialog in the sheet style: red tag, bold title, body, then actions.
Future<T?> showOpsDialog<T>(
  BuildContext context, {
  required String tag,
  required String title,
  required String body,
  required Widget Function(BuildContext ctx) actions,
  Widget Function(BuildContext ctx)? field,
}) {
  return showDialog<T>(
    context: context,
    barrierColor: Colors.black.withValues(alpha: 0.6),
    builder: (ctx) {
      final theme = Theme.of(ctx);
      final cs = theme.colorScheme;
      return Dialog(
        backgroundColor: Colors.transparent,
        child: OpsSheet(
          maxWidth: 520,
          padding: const EdgeInsets.fromLTRB(36, 32, 36, 28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(tag, style: opsLabelStyle(ctx).copyWith(color: cs.primary)),
              const SizedBox(height: 12),
              Text(
                title,
                style: theme.textTheme.headlineSmall!.copyWith(
                  fontWeight: FontWeight.w700,
                  color: AppTheme.ink(ctx),
                  height: 1.25,
                ),
              ),
              const SizedBox(height: 10),
              Text(
                body,
                style: theme.textTheme.bodyLarge!.copyWith(
                  color: cs.onSurfaceVariant,
                ),
              ),
              if (field != null) ...[const SizedBox(height: 20), field(ctx)],
              const SizedBox(height: 24),
              actions(ctx),
            ],
          ),
        ),
      );
    },
  );
}
