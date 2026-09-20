import 'package:flutter/foundation.dart';

/// Which surface is showing. Screens deep-link through this — a phase on
/// the brief opens the route scoped to it; a task opens its agent session.
class AppNav extends ChangeNotifier {
  // Budget sits beside the brief: setting the points is part of writing it.
  static const home = 0, inbox = 1, brief = 2, budget = 3, route = 4, map = 5, agents = 6, providers = 7, style = 8;

  int tab = home;

  /// Where a full-window surface (settings) was opened from.
  int _before = home;

  void go(int t) {
    if (t == tab) return;
    if (t == style) _before = tab;
    tab = t;
    notifyListeners();
  }

  /// Leave settings for the surface it covered.
  void closeSettings() => go(_before == style ? home : _before);
}
