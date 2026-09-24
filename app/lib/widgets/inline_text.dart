import 'package:flutter/material.dart';

import '../cg/inline_editable_text/inline_editable_text.dart';

import '../theme/app_theme.dart';

/// The app's click-to-edit line: [InlineEditableText] dressed in the
/// theme. Ink for the value, accent border while editing, pencil on hover.
class InlineText extends StatelessWidget {
  const InlineText({
    super.key,
    required this.value,
    required this.onChanged,
    this.onCommit,
    required this.resetKey,
    this.style,
    this.placeholder = '',
    this.autofocus = false,
    this.compact = false,
    this.textAlign = TextAlign.start,
  });

  final String value;
  final ValueChanged<String> onChanged;

  /// Once, when the edit ends (see the widget). For costly writes.
  final ValueChanged<String>? onCommit;
  final Object resetKey;
  final TextStyle? style;
  final String placeholder;
  final bool autofocus;

  /// No hover pencil, so the field's width never changes.
  final bool compact;
  final TextAlign textAlign;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    return InlineEditableText(
      value: value,
      onChanged: onChanged,
      onCommit: onCommit,
      resetKey: resetKey,
      style: style ?? theme.textTheme.bodyMedium!,
      placeholder: placeholder,
      autofocus: autofocus,
      textAlign: textAlign,
      textColor: AppTheme.ink(context),
      placeholderColor: cs.outline,
      // Faint on black, full-strength on white or it disappears.
      underlineColor: cs.outline.withValues(
        alpha: theme.brightness == Brightness.dark ? 0.6 : 1.0,
      ),
      hoverBackgroundColor: cs.surfaceContainerHigh,
      hoverBorderColor: cs.outline,
      activeBorderColor: cs.primary,
      selectionColor: cs.primary.withValues(alpha: 0.28),
      editIndicator: compact
          ? null
          : Icon(Icons.edit_outlined, size: 14, color: cs.onSurfaceVariant),
    );
  }
}
