import 'dart:io';
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/widgets.dart';

/// A hand signature as strokes: each stroke a list of `[x, y]` points,
/// normalised to 0..1 of the pad they were drawn on. Plain lists so the
/// value round-trips through JSON without a codec.
typedef SignatureStrokes = List<List<List<double>>>;

/// Every appearance value the signature widgets use. Defaults are quiet
/// greys on white; a consumer passes its own to match its design system.
class SignatureStyle {
  const SignatureStyle({
    this.ink = const Color(0xFF1A1A1A),
    this.rule = const Color(0xFF9A9A9A),
    this.guide = const Color(0xFFD6D6D6),
    this.padFill = const Color(0xFFF6F6F6),
    this.padBorder = const Color(0xFF9A9A9A),
    this.padRadius = 4,
    this.strokeWidth = 1.8,
    this.handFontFamily,
    this.labelStyle = const TextStyle(fontSize: 11, letterSpacing: 1.2, color: Color(0xFF6B6B6B)),
    this.nameStyle = const TextStyle(fontSize: 14, fontWeight: FontWeight.w700, color: Color(0xFF1A1A1A)),
    this.captionStyle = const TextStyle(fontSize: 11, color: Color(0xFF6B6B6B)),
    this.actionStyle = const TextStyle(fontSize: 11, letterSpacing: 1.2, fontWeight: FontWeight.w700, color: Color(0xFF1A1A1A)),
    this.disabledActionStyle = const TextStyle(fontSize: 11, letterSpacing: 1.2, fontWeight: FontWeight.w700, color: Color(0xFFB0B0B0)),
    this.ruleWidth = 260,
    this.padHint = 'sign here',
    this.padHelp = 'Mouse or touch. Slow strokes read best.',
    this.clearLabel = 'CLEAR',
  });

  /// Width over height of a pad and of every mark drawn from it, so what
  /// was signed is what appears. The default for every `aspect` parameter.
  static const double defaultAspect = 3.2;

  /// Colour the strokes and the typed hand are drawn in.
  final Color ink;

  /// The line under the mark in a [SignatureBlock].
  final Color rule;

  /// The faint baseline inside the pad.
  final Color guide;
  final Color padFill;
  final Color padBorder;
  final double padRadius;
  final double strokeWidth;

  /// Font used when there is no drawn or uploaded mark and the name is set
  /// in a hand instead. Null falls back to the ambient font.
  final String? handFontFamily;
  final TextStyle labelStyle;
  final TextStyle nameStyle;
  final TextStyle captionStyle;
  final TextStyle actionStyle;
  final TextStyle disabledActionStyle;
  final double ruleWidth;
  final String padHint;
  final String padHelp;
  final String clearLabel;
}

/// Paints strokes into whatever box it is given, letterboxed to [aspect]
/// so the hand is never stretched.
class SignatureStrokePainter extends CustomPainter {
  SignatureStrokePainter(this.strokes, this.color, {this.strokeWidth = 1.8, this.aspect = SignatureStyle.defaultAspect});
  final SignatureStrokes strokes;
  final Color color;
  final double strokeWidth;
  final double aspect;

  @override
  void paint(Canvas canvas, Size size) {
    var w = size.width, h = w / aspect;
    if (h > size.height) {
      h = size.height;
      w = h * aspect;
    }
    final dx = (size.width - w) / 2, dy = (size.height - h) / 2;
    final paint = Paint()
      ..color = color
      ..strokeWidth = strokeWidth
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round
      ..style = PaintingStyle.stroke;
    for (final s in strokes) {
      if (s.isEmpty) continue;
      final path = Path()..moveTo(dx + s.first[0] * w, dy + s.first[1] * h);
      for (final p in s.skip(1)) {
        path.lineTo(dx + p[0] * w, dy + p[1] * h);
      }
      canvas.drawPath(path, paint);
    }
  }

  @override
  bool shouldRepaint(covariant SignatureStrokePainter old) =>
      old.strokes != strokes || old.color != color || old.strokeWidth != strokeWidth || old.aspect != aspect;
}

/// Renders strokes to a PNG exactly as [SignatureMark] draws them, so a
/// mark filed with a document is the one that was on screen. Transparent
/// background. Null when there is nothing to draw.
Future<Uint8List?> signatureStrokesToPng(
  SignatureStrokes strokes, {
  int width = 640,
  Color color = const Color(0xFF1A1A1A),
  double aspect = SignatureStyle.defaultAspect,
  double? strokeWidth,
}) async {
  if (strokes.isEmpty) return null;
  final size = Size(width.toDouble(), width / aspect);
  final rec = ui.PictureRecorder();
  final canvas = Canvas(rec, Offset.zero & size);
  SignatureStrokePainter(strokes, color, strokeWidth: strokeWidth ?? width / 180, aspect: aspect).paint(canvas, size);
  final img = await rec.endRecording().toImage(size.width.toInt(), size.height.toInt());
  final data = await img.toByteData(format: ui.ImageByteFormat.png);
  return data?.buffer.asUint8List();
}

/// The mark alone. Precedence: an image file, then drawn strokes, then the
/// name set in the hand font. Sized by the caller through [height].
class SignatureMark extends StatelessWidget {
  const SignatureMark({
    super.key,
    required this.strokes,
    required this.name,
    this.image,
    this.height = 44,
    this.style = const SignatureStyle(),
    this.aspect = SignatureStyle.defaultAspect,
    this.imageProvider,
  });
  final SignatureStrokes strokes;
  final String name;

  /// Path of an image file: an uploaded mark, or one filed with a document.
  final String? image;
  final double height;
  final SignatureStyle style;
  final double aspect;

  /// How [image] is loaded. Default: a file on disk. A web consumer passes
  /// something that turns the path into a network image.
  final ImageProvider Function(String image)? imageProvider;

  @override
  Widget build(BuildContext context) {
    final img = image;
    if (img != null && img.isNotEmpty) {
      // No stat per frame: a file that is missing or unreadable falls
      // through to the next form when the image fails to load.
      return SizedBox(
        height: height,
        width: height * aspect,
        child: Image(
          image: imageProvider?.call(img) ?? FileImage(File(img)),
          fit: BoxFit.contain,
          alignment: Alignment.centerLeft,
          filterQuality: FilterQuality.medium,
          errorBuilder: (_, __, ___) => _fallback(),
        ),
      );
    }
    return _fallback();
  }

  Widget _fallback() {
    if (strokes.isNotEmpty) {
      return SizedBox(
        height: height,
        width: height * aspect,
        child: CustomPaint(painter: SignatureStrokePainter(strokes, style.ink, strokeWidth: style.strokeWidth, aspect: aspect)),
      );
    }
    return Text(
      name,
      style: TextStyle(fontFamily: style.handFontFamily, fontSize: height * 0.55, color: style.ink, height: 1.4),
    );
  }
}

/// A signature block as it sits on a document: a label, the mark over a
/// rule, the printed name, then title and date on one line.
class SignatureBlock extends StatelessWidget {
  const SignatureBlock({
    super.key,
    required this.strokes,
    required this.name,
    required this.title,
    required this.signedAt,
    this.image,
    this.label = 'SIGNED',
    this.style = const SignatureStyle(),
    this.markHeight = 44,
    this.dateFormat = formatSignatureDate,
    this.imageProvider,
  });

  /// From what a document stored: `"Name, Title"` and the filed mark. The
  /// title is everything after the first `", "`.
  factory SignatureBlock.stored({
    Key? key,
    required String signedBy,
    required DateTime? signedAt,
    String? image,
    String label = 'SIGNED',
    SignatureStyle style = const SignatureStyle(),
    double markHeight = 44,
    String Function(DateTime) dateFormat = formatSignatureDate,
    ImageProvider Function(String image)? imageProvider,
  }) {
    final (name, title) = splitSignedBy(signedBy);
    return SignatureBlock(
      key: key,
      strokes: const [],
      name: name,
      title: title,
      signedAt: signedAt,
      image: image,
      label: label,
      style: style,
      markHeight: markHeight,
      dateFormat: dateFormat,
      imageProvider: imageProvider,
    );
  }

  final SignatureStrokes strokes;
  final String name;
  final String title;
  final DateTime? signedAt;
  final String? image;
  final String label;
  final SignatureStyle style;
  final double markHeight;
  final String Function(DateTime) dateFormat;
  final ImageProvider Function(String image)? imageProvider;

  @override
  Widget build(BuildContext context) {
    final at = signedAt;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(label, style: style.labelStyle),
        const SizedBox(height: 6),
        SignatureMark(strokes: strokes, name: name, image: image, height: markHeight, style: style, imageProvider: imageProvider),
        Container(width: style.ruleWidth, height: 1, color: style.rule, margin: const EdgeInsets.only(top: 2, bottom: 6)),
        Text(name, style: style.nameStyle),
        Text(
          [if (title.isNotEmpty) title, if (at != null) dateFormat(at)].join(' · '),
          style: style.captionStyle,
        ),
      ],
    );
  }
}

/// `"Name, Title"` → `(name, title)`; a value without `", "` is all name.
(String, String) splitSignedBy(String signedBy) {
  final cut = signedBy.indexOf(', ');
  if (cut < 0) return (signedBy.trim(), '');
  return (signedBy.substring(0, cut).trim(), signedBy.substring(cut + 2).trim());
}

/// `14 March 2026`, in local time.
String formatSignatureDate(DateTime d) {
  final l = d.toLocal();
  const months = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
  ];
  return '${l.day} ${months[l.month - 1]} ${l.year}';
}

/// Where a signature is drawn: a pad the width of its box at [aspect],
/// mouse or touch, strokes normalised as they are laid down. Reports the
/// full stroke list at the end of each stroke and on clear.
class SignaturePad extends StatefulWidget {
  const SignaturePad({
    super.key,
    required this.strokes,
    required this.onChanged,
    this.style = const SignatureStyle(),
    this.aspect = SignatureStyle.defaultAspect,
    this.guideInset = 24,
    this.guideFromBottom = 0.28,
  });
  final SignatureStrokes strokes;
  final ValueChanged<SignatureStrokes> onChanged;
  final SignatureStyle style;
  final double aspect;

  /// Horizontal inset of the baseline inside the pad.
  final double guideInset;

  /// Where the baseline sits, as a fraction of the pad height from the bottom.
  final double guideFromBottom;

  @override
  State<SignaturePad> createState() => _SignaturePadState();
}

class _SignaturePadState extends State<SignaturePad> {
  late SignatureStrokes strokes = _copy(widget.strokes);
  Size? size;

  static SignatureStrokes _copy(SignatureStrokes s) => [
        for (final stroke in s) [for (final p in stroke) [p[0], p[1]]],
      ];

  @override
  void didUpdateWidget(covariant SignaturePad old) {
    super.didUpdateWidget(old);
    // A caller that replaces the value (load, clear from outside) wins.
    if (!identical(old.strokes, widget.strokes) && widget.strokes.isEmpty && strokes.isNotEmpty) {
      strokes = [];
    }
  }

  void _start(Offset o) => setState(() => strokes.add([_norm(o)]));
  void _move(Offset o) => setState(() => strokes.last.add(_norm(o)));
  void _end() => widget.onChanged(_copy(strokes));
  List<double> _norm(Offset o) {
    final s = size!;
    return [(o.dx / s.width).clamp(0.0, 1.0), (o.dy / s.height).clamp(0.0, 1.0)];
  }

  void _clear() {
    setState(() => strokes = []);
    widget.onChanged(const []);
  }

  @override
  Widget build(BuildContext context) {
    final st = widget.style;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        LayoutBuilder(
          builder: (context, c) {
            final h = c.maxWidth / widget.aspect;
            size = Size(c.maxWidth, h);
            return GestureDetector(
              onPanStart: (d) => _start(d.localPosition),
              onPanUpdate: (d) => _move(d.localPosition),
              onPanEnd: (_) => _end(),
              child: Container(
                height: h,
                decoration: BoxDecoration(
                  color: st.padFill,
                  border: Border.all(color: st.padBorder),
                  borderRadius: BorderRadius.circular(st.padRadius),
                ),
                child: Stack(
                  children: [
                    Positioned(
                      left: widget.guideInset,
                      right: widget.guideInset,
                      bottom: h * widget.guideFromBottom,
                      child: Container(height: 1, color: st.guide),
                    ),
                    Positioned.fill(
                      child: CustomPaint(
                        painter: SignatureStrokePainter(strokes, st.ink, strokeWidth: st.strokeWidth, aspect: widget.aspect),
                      ),
                    ),
                    if (strokes.isEmpty) Center(child: Text(st.padHint, style: st.captionStyle)),
                  ],
                ),
              ),
            );
          },
        ),
        const SizedBox(height: 6),
        Row(
          children: [
            Expanded(child: Text(st.padHelp, style: st.captionStyle)),
            GestureDetector(
              onTap: strokes.isEmpty ? null : _clear,
              behavior: HitTestBehavior.opaque,
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
                child: Text(st.clearLabel, style: strokes.isEmpty ? st.disabledActionStyle : st.actionStyle),
              ),
            ),
          ],
        ),
      ],
    );
  }
}
