/// Facts about the desk this app is showing, set once at boot: on the
/// desktop from the process; in a browser from the engine's `hello`.
abstract final class Desk {
  /// The home directory on the machine that runs the loops, so paths can
  /// be shown as `~/...`. Empty until known.
  static String home = '';

  /// How to show a file the desk holds (a signature image). On the desk
  /// itself this is null and files are read directly; in a browser it is
  /// the engine's `/file` URL for the path.
  static String Function(String path)? fileUrl;

  /// A path for reading: `~` for home.
  static String short(String path) =>
      home.isNotEmpty && path.startsWith(home) ? '~${path.substring(home.length)}' : path;
}
