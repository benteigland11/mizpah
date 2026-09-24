import 'package:flutter_test/flutter_test.dart';

import 'package:app_paths/app_paths.dart';

/// Where one app keeps its settings file and its state journal on each
/// platform, from a fake environment. A real caller passes
/// `Platform.environment` and `Platform.operatingSystem`.
void main() {
  test('example: the same app on three platforms', () {
    const app = 'sample-app';
    final linux = resolveAppPaths(app, environ: {'HOME': '/home/user'}, platform: 'linux');
    final mac = resolveAppPaths(app, vendorName: 'Example', environ: {'HOME': '/Users/user'}, platform: 'macos');
    final win = resolveAppPaths(app, vendorName: 'Example', environ: {'USERPROFILE': r'C:\Users\user'}, platform: 'windows');

    // The settings file and the state journal, per platform.
    expect('${linux.config}/settings.json', '/home/user/.config/sample-app/settings.json');
    expect('${linux.state}/runs.jsonl', '/home/user/.local/state/sample-app/runs.jsonl');
    expect('${mac.config}/settings.json',
        '/Users/user/Library/Application Support/Example/sample-app/settings.json');
    expect('${win.config}\\settings.json',
        r'C:\Users\user\AppData\Roaming\Example\sample-app\settings.json');
    expect(win.state, r'C:\Users\user\AppData\Local\Example\sample-app\State');
  });
}
