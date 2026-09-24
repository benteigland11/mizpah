import 'package:flutter/material.dart';

import '../cg/diff_line_list/diff_line_list.dart';
import '../cg/ops_typography_kit/ops_typography_kit.dart';
import '../cg/unsaved_changes_guard/unsaved_changes_guard.dart';
import 'app_theme.dart';

/// Every place the theme is turned into a Cartograph widget's style
/// object, in one file. The widgets stay theme-free; this is the bridge.

/// Section heading: the loudest thing on a page after its title.
TextStyle opsHeadingStyle(BuildContext context) => TextStyle(
  fontFamily: AppTheme.uiFamily,
  fontSize: 21,
  fontWeight: FontWeight.w700,
  letterSpacing: 2.2,
  color: Theme.of(context).colorScheme.onSurfaceVariant,
);

/// Label: small mono caption — tags, counts, sidebar headers.
TextStyle opsLabelStyle(BuildContext context) => AppTheme.mono.copyWith(
  fontSize: 12,
  letterSpacing: 1.6,
  fontWeight: FontWeight.w500,
  color: Theme.of(context).colorScheme.onSurfaceVariant,
);

/// Key: the smallest caption, sitting over a value.
TextStyle opsKeyStyle(BuildContext context) =>
    opsLabelStyle(context).copyWith(fontSize: 11);

OpsStyle opsStyle(BuildContext context) {
  final cs = Theme.of(context).colorScheme;
  return OpsStyle(
    labelStyle: opsLabelStyle(context),
    headingStyle: opsHeadingStyle(context),
    valueStyle: AppTheme.mono.copyWith(fontSize: 16, color: cs.onSurface),
    indexStyle: AppTheme.mono.copyWith(
      fontSize: 13,
      color: cs.onSurfaceVariant,
    ),
    badgeStyle: AppTheme.mono.copyWith(fontSize: 13, letterSpacing: 1),
    ruleColor: cs.outlineVariant,
    outlineColor: cs.outline,
    textColor: cs.onSurface,
    accentColor: cs.primary,
  );
}

DiffStyle diffStyle(BuildContext context) {
  final theme = Theme.of(context);
  final cs = theme.colorScheme;
  return DiffStyle(
    textStyle: theme.textTheme.bodyLarge!.copyWith(
      fontSize: 17,
      color: AppTheme.ink(context),
    ),
    contextColor: cs.onSurfaceVariant,
    addedColor: AppTheme.added(context),
    removedColor: AppTheme.removed(context),
    markStyle: AppTheme.mono.copyWith(
      fontSize: 15,
      fontWeight: FontWeight.w700,
    ),
    indexStyle: AppTheme.mono.copyWith(
      fontSize: 13,
      color: cs.onSurfaceVariant,
    ),
    trailingStyle: AppTheme.mono.copyWith(fontSize: 13, letterSpacing: 1),
  );
}

PromptStyle promptStyle(BuildContext context) {
  final theme = Theme.of(context);
  final cs = theme.colorScheme;
  return PromptStyle(
    background: cs.surfaceContainer,
    outline: cs.outline,
    tagStyle: opsLabelStyle(context).copyWith(color: cs.primary),
    titleStyle: theme.textTheme.headlineSmall!.copyWith(
      fontWeight: FontWeight.w700,
      color: AppTheme.ink(context),
      height: 1.25,
    ),
    bodyStyle: theme.textTheme.bodyLarge!.copyWith(color: cs.onSurfaceVariant),
    primaryButton: ButtonLook(
      background: cs.primary,
      foreground: cs.onPrimary,
      textStyle: theme.textTheme.labelLarge!,
    ),
    dangerButton: ButtonLook(
      outline: AppTheme.removed(context),
      foreground: AppTheme.removed(context),
      textStyle: theme.textTheme.labelLarge!,
    ),
    quietButton: ButtonLook(
      foreground: cs.onSurface,
      textStyle: theme.textTheme.labelLarge!,
    ),
  );
}
