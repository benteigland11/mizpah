import 'package:flutter/material.dart';

import '../cg/unsaved_changes_guard/unsaved_changes_guard.dart';
import '../state/brief_manager.dart';
import '../theme/kit_styles.dart';

/// Ask before an action would drop unsaved edits. Resolves true when it's
/// fine to proceed (nothing dirty, saved, or discarded), false to stay.
Future<bool> confirmLeave(BuildContext context, BriefManager manager) =>
    guardUnsaved(
      dirty: manager.dirty,
      ask: () => showDialog<LeaveChoice>(
        context: context,
        barrierColor: Colors.black.withValues(alpha: 0.6),
        builder: (ctx) => Dialog(
          backgroundColor: Colors.transparent,
          child: UnsavedChangesPrompt(
            subject: manager.draft?.title ?? 'This brief',
            style: promptStyle(ctx),
            onChoice: (c) => Navigator.pop(ctx, c),
          ),
        ),
      ),
      save: () async {
        await manager.save();
        return manager.error == null;
      },
      discard: manager.revert,
    );
