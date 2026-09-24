// One `boot()` for both worlds: in a process with the files, or in a
// browser talking to it. The compiler picks by platform.
export 'desk_boot.dart' if (dart.library.js_interop) 'web_boot.dart';
export 'kinds.dart';
