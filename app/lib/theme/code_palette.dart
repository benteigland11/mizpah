import 'package:flutter/material.dart';

import '../cg/syntax_highlighted_code/syntax_highlighted_code.dart';
import 'app_theme.dart';

/// Code in the desk's colours: keys in the system cyan, strings in pass
/// green, numbers and literals in hold amber, keywords in the accent,
/// names in ink, comments and punctuation receding.
HighlightPalette codePalette(BuildContext context) {
  final cs = Theme.of(context).colorScheme;
  final b = cs.brightness;
  return HighlightPalette(
    keyword: cs.primary,
    string: AppTheme.pass(b),
    number: AppTheme.hold(b),
    literal: AppTheme.hold(b),
    comment: cs.outline,
    key: AppTheme.system(b),
    title: AppTheme.ink(context),
    meta: cs.onSurfaceVariant,
    punctuation: cs.onSurfaceVariant,
    addition: AppTheme.added(context),
    deletion: AppTheme.removed(context),
  );
}
