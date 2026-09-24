import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../cg/signature_pad/signature_pad.dart' as cg;
import '../engine/desk.dart';
import '../theme/app_theme.dart';
import '../theme/kit_styles.dart';

/// Strokes normalised to 0..1 of the pad they were drawn on.
typedef Strokes = cg.SignatureStrokes;

/// The signature widget dressed in the app's paper look: ink for the hand,
/// the ops label styles, the bundled script font when nothing is drawn.
cg.SignatureStyle signatureStyle(BuildContext context) {
  final cs = Theme.of(context).colorScheme;
  final ink = AppTheme.ink(context);
  return cg.SignatureStyle(
    ink: ink,
    rule: cs.outline,
    guide: cs.outlineVariant,
    padFill: cs.surfaceContainerLow,
    padBorder: cs.outline,
    padRadius: AppTheme.radius,
    handFontFamily: 'Homemade Apple',
    labelStyle: opsKeyStyle(context),
    nameStyle: Theme.of(context).textTheme.bodyMedium!.copyWith(color: ink, fontWeight: FontWeight.w700),
    captionStyle: opsKeyStyle(context),
    actionStyle: opsLabelStyle(context),
    disabledActionStyle: opsLabelStyle(context).copyWith(color: cs.outline),
  );
}

/// Where a mark's image comes from: the desk's file, or its URL from afar.
ImageProvider Function(String)? get _images => Desk.fileUrl == null ? null : (p) => NetworkImage(Desk.fileUrl!(p));

/// Strokes to PNG in document ink, for filing with a document.
Future<Uint8List?> strokesToPng(Strokes strokes) => cg.signatureStrokesToPng(strokes, color: const Color(0xFF1A1A1A));

/// The mark alone: an uploaded image, drawn strokes, or the name in a
/// hand when there is neither.
class SignatureMark extends StatelessWidget {
  const SignatureMark({super.key, required this.strokes, required this.name, this.image, this.height = 44});
  final Strokes strokes;
  final String name;
  final String? image;
  final double height;

  @override
  Widget build(BuildContext context) =>
      cg.SignatureMark(strokes: strokes, name: name, image: image, height: height, style: signatureStyle(context), imageProvider: _images);
}

/// A signature block as it sits on a document.
class SignatureBlock extends StatelessWidget {
  const SignatureBlock({
    super.key,
    required this.strokes,
    required this.name,
    required this.title,
    required this.signedAt,
    this.image,
    this.label = 'SIGNED',
  });

  /// From what a document stored: "Name, Title" and the filed mark.
  factory SignatureBlock.stored({Key? key, required String signedBy, String? image, required DateTime? signedAt, String label = 'SIGNED'}) {
    final (name, title) = cg.splitSignedBy(signedBy);
    return SignatureBlock(key: key, strokes: const [], name: name, title: title, signedAt: signedAt, image: image, label: label);
  }

  final Strokes strokes;
  final String name;
  final String title;
  final DateTime? signedAt;
  final String? image;
  final String label;

  @override
  Widget build(BuildContext context) => cg.SignatureBlock(
        strokes: strokes,
        name: name,
        title: title,
        signedAt: signedAt,
        image: image,
        label: label,
        style: signatureStyle(context),
        imageProvider: _images,
      );
}

/// Where the signature is drawn once.
class SignaturePad extends StatelessWidget {
  const SignaturePad({super.key, required this.strokes, required this.onChanged});
  final Strokes strokes;
  final ValueChanged<Strokes> onChanged;

  @override
  Widget build(BuildContext context) => cg.SignaturePad(strokes: strokes, onChanged: onChanged, style: signatureStyle(context));
}
