import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';

/// Text that becomes an editor when tapped and text again when it loses
/// focus, without changing size in between.
///
/// Editing semantics:
///  * every keystroke is reported through [onChanged], so the caller's
///    model stays live (dirty flags, autosave, …);
///  * Enter commits (drops focus), Shift+Enter inserts a line break;
///  * Escape restores the value the edit started from and commits that.
///
/// The view and edit states share one box - same padding, same border
/// slot - so entering edit mode never shifts the surrounding layout.
///
/// Appearance is fully parameterised; defaults are neutral greys so the
/// widget is usable without any design system.
class InlineEditableText extends StatefulWidget {
  /// Creates an inline editable text.
  const InlineEditableText({
    super.key,
    required this.value,
    required this.onChanged,
    this.onCommit,
    this.resetKey,
    this.style = const TextStyle(fontSize: 14),
    this.placeholder = '',
    this.autofocus = false,
    this.textAlign = TextAlign.start,
    this.textColor,
    this.placeholderColor = const Color(0xFF8A8A8A),
    this.underlineColor = const Color(0x998A8A8A),
    this.hoverBackgroundColor = const Color(0x14808080),
    this.hoverBorderColor = const Color(0xFF8A8A8A),
    this.activeBorderColor = const Color(0xFF3B82F6),
    this.cursorColor,
    this.selectionColor = const Color(0x553B82F6),
    this.padding = const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
    this.borderRadius = 3,
    this.borderWidth = 1,
    this.animationDuration = const Duration(milliseconds: 80),
    this.editIndicator,
  });

  /// The current value, owned by the caller.
  final String value;

  /// Called on every change while editing, and once more with the restored
  /// value when an edit is cancelled with Escape.
  final ValueChanged<String> onChanged;

  /// Reported once, when an edit ends with Enter or by losing focus, with
  /// the final text — and only if it differs from what the edit started
  /// from. Not reported after Escape. For callers whose write is costly
  /// (a file, a process) and must not run per keystroke.
  final ValueChanged<String>? onCommit;

  /// When this changes, the editor's text is resynchronised from [value].
  /// Use it to push a wholesale replacement (load, revert) into a field
  /// that may currently be editing, without fighting the user's typing.
  final Object? resetKey;

  /// Style for the value in both modes. [textColor] overrides its colour.
  final TextStyle style;

  /// Shown, in [placeholderColor], when [value] is empty.
  final String placeholder;

  /// Start in edit mode (for a row that was just created).
  final bool autofocus;

  /// Alignment in both modes.
  final TextAlign textAlign;

  /// Colour of the value text. Defaults to [style]'s colour.
  final Color? textColor;

  /// Colour of the placeholder.
  final Color placeholderColor;

  /// Dotted underline drawn under the value in view mode, as a standing cue
  /// that the line is editable. Transparent to disable.
  final Color underlineColor;

  /// Background while hovered or editing.
  final Color hoverBackgroundColor;

  /// Border while hovered (view mode).
  final Color hoverBorderColor;

  /// Border while editing.
  final Color activeBorderColor;

  /// Caret colour. Defaults to [activeBorderColor].
  final Color? cursorColor;

  /// Highlight behind selected text.
  final Color selectionColor;

  /// Inner padding, identical in both modes.
  final EdgeInsets padding;

  /// Corner radius of the box.
  final double borderRadius;

  /// Border width; reserved in view mode (transparent) so nothing shifts.
  final double borderWidth;

  /// Duration of the hover/edit background and border transition.
  final Duration animationDuration;

  /// Optional widget shown at the trailing edge while hovered in view
  /// mode, e.g. a small pencil icon. Omit for fields whose width must
  /// never change (a number followed by a unit).
  final Widget? editIndicator;

  @override
  State<InlineEditableText> createState() => _InlineEditableTextState();
}

class _InlineEditableTextState extends State<InlineEditableText>
    implements TextSelectionGestureDetectorBuilderDelegate {
  late bool _editing = widget.autofocus;

  // Mouse/touch selection (click-drag, double-click a word, shift-click)
  // is a layer TextField adds over EditableText; we add it ourselves.
  @override
  final GlobalKey<EditableTextState> editableTextKey =
      GlobalKey<EditableTextState>();
  @override
  bool get forcePressEnabled => false;
  @override
  bool get selectionEnabled => true;
  late final _selectionGestures =
      TextSelectionGestureDetectorBuilder(delegate: this);

  bool _hover = false;
  late final TextEditingController _controller =
      TextEditingController(text: widget.value);
  final FocusNode _focus = FocusNode();
  String _startValue = '';

  @override
  void initState() {
    super.initState();
    _focus.addListener(_onFocus);
    if (_editing) _startValue = widget.value;
  }

  bool _cancelled = false;

  void _onFocus() {
    if (!_focus.hasFocus && _editing) {
      setState(() => _editing = false);
      final text = _controller.text;
      if (!_cancelled && text != _startValue) widget.onCommit?.call(text);
      _cancelled = false;
    }
  }

  @override
  void didUpdateWidget(InlineEditableText old) {
    super.didUpdateWidget(old);
    if (old.resetKey != widget.resetKey &&
        _controller.text != widget.value) {
      _controller.text = widget.value;
    }
  }

  @override
  void dispose() {
    _focus.removeListener(_onFocus);
    _focus.dispose();
    _controller.dispose();
    super.dispose();
  }

  void _begin() {
    _startValue = widget.value;
    if (_controller.text != widget.value) _controller.text = widget.value;
    setState(() => _editing = true);
  }

  void _commit() => _focus.unfocus();

  void _cancel() {
    _controller.text = _startValue;
    widget.onChanged(_startValue);
    _cancelled = true;
    _focus.unfocus();
  }

  void _newline() {
    final v = _controller.value;
    final sel = v.selection;
    final start = sel.isValid ? sel.start : v.text.length;
    final end = sel.isValid ? sel.end : v.text.length;
    final text = v.text.replaceRange(start, end, '\n');
    _controller.value = v.copyWith(
      text: text,
      selection: TextSelection.collapsed(offset: start + 1),
      composing: TextRange.empty,
    );
    widget.onChanged(text);
  }

  @override
  Widget build(BuildContext context) {
    final style = widget.textColor == null
        ? widget.style
        : widget.style.copyWith(color: widget.textColor);
    final empty = widget.value.isEmpty;
    final active = _editing || _hover;

    final Widget content = _editing
        ? CallbackShortcuts(
            bindings: {
              const SingleActivator(LogicalKeyboardKey.escape): _cancel,
              const SingleActivator(LogicalKeyboardKey.enter): _commit,
              const SingleActivator(LogicalKeyboardKey.enter, shift: true):
                  _newline,
            },
            child: _selectionGestures.buildGestureDetector(
              behavior: HitTestBehavior.translucent,
              child: EditableText(
                key: editableTextKey,
                controller: _controller,
                focusNode: _focus,
                autofocus: true,
                onChanged: widget.onChanged,
                style: style,
                textAlign: widget.textAlign,
                maxLines: null,
                cursorColor: widget.cursorColor ?? widget.activeBorderColor,
                backgroundCursorColor: widget.hoverBackgroundColor,
                selectionColor: widget.selectionColor,
                rendererIgnoresPointer: true,
              ),
            ),
          )
        : Text(
            empty ? widget.placeholder : widget.value,
            textAlign: widget.textAlign,
            // A placeholder is a prompt, not a value: greyed and italic, so it
            // never reads as something already written.
            style: (empty ? style.copyWith(color: widget.placeholderColor, fontStyle: FontStyle.italic) : style)
                .copyWith(
              decoration: TextDecoration.underline,
              decorationStyle: TextDecorationStyle.dotted,
              decorationColor:
                  _hover ? const Color(0x00000000) : widget.underlineColor,
            ),
          );

    return MouseRegion(
      cursor: SystemMouseCursors.text,
      onEnter: (_) => setState(() => _hover = true),
      onExit: (_) => setState(() => _hover = false),
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: _editing ? null : _begin,
        child: AnimatedContainer(
          duration: widget.animationDuration,
          padding: widget.padding,
          decoration: BoxDecoration(
            color: active ? widget.hoverBackgroundColor : null,
            borderRadius: BorderRadius.circular(widget.borderRadius),
            border: Border.all(
              width: widget.borderWidth,
              color: _editing
                  ? widget.activeBorderColor
                  : _hover
                      ? widget.hoverBorderColor
                      : const Color(0x00000000),
            ),
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(child: content),
              if (_hover && !_editing && widget.editIndicator != null)
                Padding(
                  padding: const EdgeInsets.only(left: 8, top: 2),
                  child: widget.editIndicator,
                ),
            ],
          ),
        ),
      ),
    );
  }
}
