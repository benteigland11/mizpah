/// Guard an action that would drop unsaved edits: ask, then save, discard
/// or stay. The decision logic ([guardUnsaved]) is independent of how the
/// question is presented; [UnsavedChangesPrompt] is a widgets.dart sheet
/// you can drop into any dialog or overlay.
library;

import 'package:flutter/widgets.dart';

/// What the user chose when asked about unsaved edits.
enum LeaveChoice { save, discard, stay }

/// Resolve an attempt to leave. Returns true when it's fine to proceed.
///
/// * not [dirty] → proceed without asking;
/// * [LeaveChoice.save] → [save] runs; proceed only if it reports success;
/// * [LeaveChoice.discard] → [discard] runs; proceed;
/// * [LeaveChoice.stay] or a dismissed prompt (null) → stay.
Future<bool> guardUnsaved({
  required bool dirty,
  required Future<LeaveChoice?> Function() ask,
  required Future<bool> Function() save,
  required void Function() discard,
}) async {
  if (!dirty) return true;
  switch (await ask()) {
    case LeaveChoice.save:
      return await save();
    case LeaveChoice.discard:
      discard();
      return true;
    case LeaveChoice.stay:
    case null:
      return false;
  }
}

/// Appearance of [UnsavedChangesPrompt]. Defaults: neutral dark sheet.
class PromptStyle {
  const PromptStyle({
    this.background = const Color(0xFF171717),
    this.outline = const Color(0xFF6A6A6A),
    this.tagStyle = const TextStyle(
        fontSize: 11, letterSpacing: 1.6, color: Color(0xFFFF6B5B)),
    this.titleStyle = const TextStyle(
        fontSize: 22, fontWeight: FontWeight.w700, color: Color(0xFFE6E6E6)),
    this.bodyStyle = const TextStyle(fontSize: 16, color: Color(0xFF8A8A8A)),
    this.primaryButton = const ButtonLook(
        background: Color(0xFFE6E6E6), foreground: Color(0xFF111111)),
    this.dangerButton = const ButtonLook(
        outline: Color(0xFFF07178), foreground: Color(0xFFF07178)),
    this.quietButton = const ButtonLook(foreground: Color(0xFFE6E6E6)),
    this.padding = const EdgeInsets.fromLTRB(36, 32, 36, 28),
    this.maxWidth = 520,
    this.borderRadius = 4,
  });

  final Color background;
  final Color outline;
  final TextStyle tagStyle;
  final TextStyle titleStyle;
  final TextStyle bodyStyle;
  final ButtonLook primaryButton;
  final ButtonLook dangerButton;
  final ButtonLook quietButton;
  final EdgeInsets padding;
  final double maxWidth;
  final double borderRadius;
}

/// Colours for one of the prompt's buttons.
class ButtonLook {
  const ButtonLook({
    this.background,
    this.outline,
    required this.foreground,
    this.padding = const EdgeInsets.symmetric(horizontal: 22, vertical: 16),
    this.textStyle = const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
  });
  final Color? background;
  final Color? outline;
  final Color foreground;
  final EdgeInsets padding;
  final TextStyle textStyle;
}

/// The question, as a sheet. Reports the choice through [onChoice]; the
/// caller closes whatever is hosting it.
class UnsavedChangesPrompt extends StatelessWidget {
  const UnsavedChangesPrompt({
    super.key,
    required this.subject,
    required this.onChoice,
    this.style = const PromptStyle(),
    this.tag = '● UNSAVED CHANGES',
    this.body = 'Leaving now drops them. Save first, or discard and go on.',
    this.saveLabel = 'SAVE AND CONTINUE',
    this.discardLabel = 'DISCARD',
    this.stayLabel = 'KEEP EDITING',
  });

  /// Name of the thing with edits, used in the title.
  final String subject;
  final ValueChanged<LeaveChoice> onChoice;
  final PromptStyle style;
  final String tag;
  final String body;
  final String saveLabel;
  final String discardLabel;
  final String stayLabel;

  @override
  Widget build(BuildContext context) {
    return ConstrainedBox(
      constraints: BoxConstraints(maxWidth: style.maxWidth),
      child: Container(
        padding: style.padding,
        decoration: BoxDecoration(
          color: style.background,
          border: Border.all(color: style.outline),
          borderRadius: BorderRadius.circular(style.borderRadius),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(tag, style: style.tagStyle),
            const SizedBox(height: 12),
            Text("$subject has edits that haven't been saved.",
                style: style.titleStyle),
            const SizedBox(height: 10),
            Text(body, style: style.bodyStyle),
            const SizedBox(height: 28),
            // Wraps rather than overflows when labels run long.
            Wrap(
              spacing: 10,
              runSpacing: 10,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                PromptButton(saveLabel, look: style.primaryButton,
                    onTap: () => onChoice(LeaveChoice.save)),
                PromptButton(discardLabel, look: style.dangerButton,
                    onTap: () => onChoice(LeaveChoice.discard)),
                PromptButton(stayLabel, look: style.quietButton,
                    onTap: () => onChoice(LeaveChoice.stay)),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

/// A plain, design-system-free button used by the prompt.
class PromptButton extends StatelessWidget {
  const PromptButton(this.label, {super.key, required this.look, required this.onTap});
  final String label;
  final ButtonLook look;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      child: GestureDetector(
        onTap: onTap,
        child: Container(
          padding: look.padding,
          decoration: BoxDecoration(
            color: look.background,
            border: look.outline == null ? null : Border.all(color: look.outline!),
            borderRadius: BorderRadius.circular(3),
          ),
          child: Text(label, style: look.textStyle.copyWith(color: look.foreground)),
        ),
      ),
    );
  }
}
