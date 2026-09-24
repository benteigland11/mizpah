import 'package:flutter/material.dart';

import '../theme/app_theme.dart';
import '../theme/kit_styles.dart';
import 'ops.dart';

/// The desk, held under a notice while something is being done on the
/// person's behalf: "ARCHIVING…". The paper stays visible and dimmed; the
/// notice takes the taps, so nothing is done twice. Over the wire an
/// action can take seconds, and a button that did nothing visible for
/// that long reads as broken.
class WorkingOver extends StatelessWidget {
  const WorkingOver({super.key, required this.working, required this.child});

  /// The verb, present tense ("Archiving"), or null when idle.
  final String? working;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Stack(
      children: [
        child,
        if (working != null)
          Positioned.fill(
            child: AbsorbPointer(
              child: ColoredBox(
                color: cs.surface.withValues(alpha: 0.55),
                child: Center(
                  child: OpsSheet(
                    maxWidth: 360,
                    padding: const EdgeInsets.fromLTRB(28, 22, 28, 22),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2, color: AppTheme.ink(context)),
                        ),
                        const SizedBox(width: 14),
                        Text('${working!.toUpperCase()}…', style: opsLabelStyle(context).copyWith(color: AppTheme.ink(context))),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }
}
