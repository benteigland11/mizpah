import 'package:flutter/foundation.dart';

/// Which surface is showing. Screens deep-link through this — a phase on
/// the brief opens the route scoped to it; a task opens its agent session.
class AppNav extends ChangeNotifier {
  // Budget sits beside the brief: setting the points is part of writing it.
  /// Stack slots, named as the tabs read. `inbox` is the in-tray across
  /// every project; `dailyWork` is one project's paper; `chat` is the
  /// Deputy's desk (Home).
  static const inbox = 0, dailyWork = 1, brief = 2, budget = 3, route = 4, map = 5, agents = 6, providers = 7, style = 8, chat = 9, procedures = 10, gyms = 11, files = 12;

  int tab = chat;

  /// Full-window surfaces: not tabs of a task but pages you step into
  /// and back out of.
  static const overlays = {providers, style};

  /// Where the current overlay was opened from.
  int _before = inbox;

  void go(int t) {
    if (t == tab) return;
    if (overlays.contains(t) && !overlays.contains(tab)) _before = tab;
    tab = t;
    notifyListeners();
  }

  /// Settings opened at a section (`signature`, `tasks`, …), for a link
  /// that means one part of it. The request number changes each time so
  /// the screen picks the section again even if it is the same one.
  String settingsSection = '';
  int settingsRequest = 0;
  void goSettings(String section) {
    settingsSection = section;
    settingsRequest++;
    if (tab == style) {
      notifyListeners();
    } else {
      go(style);
    }
  }

  /// Leave an overlay for the surface it covered.
  void closeOverlay() => go(overlays.contains(_before) ? chat : _before);

  /// Kept for callers that predate [closeOverlay].
  void closeSettings() => closeOverlay();
}
