import 'package:flutter_test/flutter_test.dart';

import 'package:app_paths/app_paths.dart';

void main() {
  const env = {'HOME': '/home/user'};

  test('linux follows XDG defaults under home', () {
    final p = resolveAppPaths('sample', environ: env, platform: 'linux');
    expect(p.config, '/home/user/.config/sample');
    expect(p.state, '/home/user/.local/state/sample');
    expect(p.cache, '/home/user/.cache/sample');
    expect(p.data, '/home/user/.local/share/sample');
  });

  test('linux honours XDG overrides and ignores the vendor', () {
    final p = resolveAppPaths(
      'sample',
      vendorName: 'Vendor',
      environ: {...env, 'XDG_STATE_HOME': '/var/state', 'XDG_CONFIG_HOME': '/etc/u'},
      platform: 'linux',
    );
    expect(p.state, '/var/state/sample');
    expect(p.config, '/etc/u/sample');
    expect(p.data, '/home/user/.local/share/sample');
  });

  test('macos uses Application Support and Caches, nesting the vendor', () {
    final p = resolveAppPaths('sample', vendorName: 'Vendor', environ: env, platform: 'darwin');
    expect(p.config, '/home/user/Library/Application Support/Vendor/sample');
    expect(p.state, p.config);
    expect(p.data, p.config);
    expect(p.cache, '/home/user/Library/Caches/Vendor/sample');
  });

  test('windows splits roaming config from local state, cache and data', () {
    final p = resolveAppPaths(
      'sample',
      vendorName: 'Vendor',
      environ: {
        'USERPROFILE': r'C:\Users\u',
        'APPDATA': r'C:\Users\u\AppData\Roaming',
        'LOCALAPPDATA': r'C:\Users\u\AppData\Local',
      },
      platform: 'win32',
    );
    expect(p.config, r'C:\Users\u\AppData\Roaming\Vendor\sample');
    expect(p.state, r'C:\Users\u\AppData\Local\Vendor\sample\State');
    expect(p.cache, r'C:\Users\u\AppData\Local\Vendor\sample\Cache');
    expect(p.data, r'C:\Users\u\AppData\Local\Vendor\sample\Data');
  });

  test('windows falls back to AppData under the profile when unset', () {
    final p = resolveAppPaths('sample', environ: {'USERPROFILE': r'C:\Users\u'}, platform: 'windows');
    expect(p.config, r'C:\Users\u\AppData\Roaming\sample');
    expect(p.state, r'C:\Users\u\AppData\Local\sample\State');
  });

  test('an explicit home wins over the environment', () {
    final p = resolveAppPaths('sample', environ: env, platform: 'linux', home: '/srv/h');
    expect(p.config, '/srv/h/.config/sample');
  });

  test('platform spellings normalise; unknown means linux', () {
    expect(normalizePlatform('Win32'), 'windows');
    expect(normalizePlatform('cygwin'), 'windows');
    expect(normalizePlatform('msys'), 'windows');
    expect(normalizePlatform('Darwin'), 'macos');
    expect(normalizePlatform('osx'), 'macos');
    expect(normalizePlatform('mac'), 'macos');
    expect(normalizePlatform('freebsd'), 'linux');
  });

  test('app names are folder-safe and never empty', () {
    expect(normalizeAppName('  My App/1.0*  '), 'My App1.0');
    expect(() => normalizeAppName('   '), throwsArgumentError);
    expect(() => resolveAppPaths('', environ: env, platform: 'linux'), throwsArgumentError);
  });

  test('no home anywhere is an error, not a guess', () {
    expect(() => resolveAppPaths('sample', environ: const {}, platform: 'linux'), throwsArgumentError);
  });

  test('value semantics', () {
    final a = resolveAppPaths('sample', environ: env, platform: 'linux');
    final b = resolveAppPaths('sample', environ: env, platform: 'linux');
    expect(a, b);
    expect(a.hashCode, b.hashCode);
    expect(a.toString(), contains('/home/user/.config/sample'));
  });
}
