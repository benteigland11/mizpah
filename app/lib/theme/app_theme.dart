import 'package:flutter/material.dart';

/// "Paper": near-monochrome surfaces, high contrast, one red accent.
/// Space Grotesk for the UI, JetBrains Mono for ids, JSON and engine output.
///
/// Because the accent is red, error states get a distinct amber so
/// "unsaved" and "failed" never read the same.
///
/// Every stock Material component the app touches is restyled here —
/// buttons, menus, tooltips, scrollbars, dialogs — so nothing on screen
/// is a Flutter default. Square corners (3px), no elevation, mono labels.
abstract final class AppTheme {
  static const uiFamily = 'Space Grotesk';
  static const monoFamily = 'JetBrains Mono';

  /// Corner radius for everything: chips, buttons, sheets, menus.
  static const radius = 3.0;

  /// Widest a document column gets before it's centred with margins.
  static const contentWidth = 1240.0;

  static const _accent = Color(0xFFC0392B);
  static const _accentDark = Color(0xFFFF6B5B);
  static const _errorLight = Color(0xFFB45309);
  static const _errorDark = Color(0xFFF5A524);

  static ThemeData light() => _build(
    ColorScheme.fromSeed(
      seedColor: _accent,
      brightness: Brightness.light,
      dynamicSchemeVariant: DynamicSchemeVariant.monochrome,
      contrastLevel: 0.5,
    ).copyWith(primary: _accent, error: _errorLight),
  );

  static ThemeData dark() => _build(
    ColorScheme.fromSeed(
      seedColor: _accent,
      brightness: Brightness.dark,
      dynamicSchemeVariant: DynamicSchemeVariant.monochrome,
      contrastLevel: 0.5,
    ).copyWith(
      primary: _accentDark,
      error: _errorDark,
      surface: const Color(0xFF111111),
      surfaceContainerLowest: const Color(0xFF0A0A0A),
      surfaceContainerLow: const Color(0xFF171717),
      surfaceContainer: const Color(0xFF1D1D1D),
      surfaceContainerHigh: const Color(0xFF242424),
      surfaceContainerHighest: const Color(0xFF2C2C2C),
    ),
  );

  static ThemeData _build(ColorScheme cs) {
    final base = ThemeData(
      colorScheme: cs,
      brightness: cs.brightness,
      fontFamily: uiFamily,
    );
    final square = RoundedRectangleBorder(
      borderRadius: BorderRadius.circular(radius),
    );
    final buttonText = mono.copyWith(
      fontSize: 12,
      letterSpacing: 1.2,
      fontWeight: FontWeight.w600,
    );
    const buttonPad = EdgeInsets.symmetric(horizontal: Sp.l, vertical: Sp.m);

    return base.copyWith(
      scaffoldBackgroundColor: cs.surface,
      splashFactory: NoSplash.splashFactory,
      inputDecorationTheme: InputDecorationTheme(
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(radius)),
        isDense: true,
      ),
      dividerTheme: DividerThemeData(color: cs.outlineVariant),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          shape: square,
          elevation: 0,
          textStyle: buttonText,
          padding: buttonPad,
          minimumSize: const Size(0, 36),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          shape: square,
          textStyle: buttonText,
          padding: buttonPad,
          minimumSize: const Size(0, 36),
          side: BorderSide(color: cs.outline),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          shape: square,
          textStyle: buttonText,
          padding: buttonPad,
          minimumSize: const Size(0, 36),
          foregroundColor: cs.onSurfaceVariant,
        ),
      ),
      iconButtonTheme: IconButtonThemeData(
        style: IconButton.styleFrom(shape: square),
      ),
      popupMenuTheme: PopupMenuThemeData(
        color: cs.surfaceContainer,
        elevation: 0,
        shape: square.copyWith(side: BorderSide(color: cs.outline)),
        textStyle: mono.copyWith(fontSize: 13, color: cs.onSurface),
        menuPadding: const EdgeInsets.symmetric(vertical: Sp.xs),
      ),
      tooltipTheme: TooltipThemeData(
        decoration: BoxDecoration(
          color: cs.surfaceContainerHighest,
          border: Border.all(color: cs.outline),
          borderRadius: BorderRadius.circular(radius),
        ),
        textStyle: mono.copyWith(fontSize: 12, color: cs.onSurface),
        padding: const EdgeInsets.symmetric(horizontal: Sp.s, vertical: Sp.xs),
      ),
      scrollbarTheme: ScrollbarThemeData(
        thickness: const WidgetStatePropertyAll(4),
        radius: const Radius.circular(2),
        thumbColor: WidgetStatePropertyAll(cs.outline),
      ),
      dialogTheme: DialogThemeData(shape: square, elevation: 0),
    );
  }

  /// Style for ids, JSON, engine output.
  static const mono = TextStyle(fontFamily: monoFamily, fontSize: 14);

  /// Diff colours: what a change would add or remove.
  static Color added(BuildContext context) =>
      Theme.of(context).brightness == Brightness.dark
      ? const Color(0xFF5FD38D)
      : const Color(0xFF1E7F4A);
  static Color removed(BuildContext context) =>
      Theme.of(context).brightness == Brightness.dark
      ? const Color(0xFFF07178)
      : const Color(0xFFB3261E);

  /// Gate colours. Green is a gate that went green — the method is verified,
  /// and everything under it is work that stands. Amber is a marker that asks
  /// you to stop and check: claimed, not yet accepted.
  static Color pass(Brightness b) =>
      b == Brightness.dark ? const Color(0xFF5FD38D) : const Color(0xFF1E7F4A);
  static Color hold(Brightness b) =>
      b == Brightness.dark ? const Color(0xFFF5C84A) : const Color(0xFF8A6100);

  /// The system talking: the host or the harness, not the worker and not
  /// the person — a handoff, a resume, a message relayed. Cyan, clear of
  /// the stop/pause/go reds, yellows and greens and of [ink].
  static Color system(Brightness b) =>
      b == Brightness.dark ? const Color(0xFF4FC3D9) : const Color(0xFF0E6F80);

  /// A deep, saturated red for a hard stop (gate red), apart from the
  /// accent's coral.
  static Color crimson(Brightness b) =>
      b == Brightness.dark ? const Color(0xFFFF2E44) : const Color(0xFF9B0F1F);

  /// A pink red for a warning that is not a stop (an estimate spent).
  static Color rose(Brightness b) =>
      b == Brightness.dark ? const Color(0xFFFF8AB3) : const Color(0xFFB0275F);

  /// Colour of text the user wrote — needs, mission, titles — as opposed
  /// to labels and chrome. Ice blue on dark, deep navy on light, so
  /// content reads as ink on paper and never blends into the grey scaffold.
  static Color ink(BuildContext context) =>
      Theme.of(context).brightness == Brightness.dark
      ? const Color(0xFFBFD9F2)
      : const Color(0xFF14213D);
}

/// Spacing scale. Use these instead of ad-hoc numbers.
abstract final class Sp {
  static const xs = 4.0;
  static const s = 8.0;
  static const m = 12.0;
  static const l = 16.0;
  static const xl = 24.0;
  static const xxl = 36.0;
}
