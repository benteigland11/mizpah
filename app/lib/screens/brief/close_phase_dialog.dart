import 'package:flutter/material.dart';

import '../../theme/app_theme.dart';
import '../../widgets/ops.dart';

/// Terra refuses a phase closure without a basis; so do we. Resolves to
/// the reason, or null if cancelled.
Future<String?> askCloseReason(BuildContext context, String phaseTitle) {
  final c = TextEditingController();
  return showOpsDialog<String>(
    context,
    tag: 'CLOSE PHASE',
    title: phaseTitle,
    body:
        'Closing is a decision, not a count. State the basis; it goes on '
        'record with the phase.',
    field: (ctx) => TextField(
      controller: c,
      autofocus: true,
      maxLines: 3,
      minLines: 2,
      style: TextStyle(color: AppTheme.ink(ctx)),
      decoration: const InputDecoration(
        hintText: 'On what basis?',
        border: OutlineInputBorder(),
      ),
      onSubmitted: (s) => s.trim().isEmpty ? null : Navigator.pop(ctx, s),
    ),
    actions: (ctx) => ValueListenableBuilder(
      valueListenable: c,
      builder: (ctx, v, _) => Row(
        children: [
          FilledButton(
            onPressed: v.text.trim().isEmpty
                ? null
                : () => Navigator.pop(ctx, v.text),
            style: FilledButton.styleFrom(
              padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 16),
            ),
            child: const Text('CLOSE PHASE'),
          ),
          const Spacer(),
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('CANCEL'),
          ),
        ],
      ),
    ),
  );
}
