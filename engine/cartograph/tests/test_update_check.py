import io
import json
import os


def test_version_comparison_handles_newer_and_prerelease():
    from cartograph.update_check import _is_newer

    assert _is_newer("0.6.8", "0.6.7")
    assert _is_newer("1.0.0", "0.9.9")
    assert not _is_newer("0.6.7", "0.6.7")
    assert not _is_newer("1.0.0rc1", "1.0.0")


def test_update_recommendation_skips_non_interactive(monkeypatch):
    from cartograph.update_check import maybe_recommend_update

    class NonTty(io.StringIO):
        def isatty(self):
            return False

    stderr = NonTty()
    monkeypatch.setattr("sys.stderr", stderr)
    monkeypatch.setattr("cartograph.update_check._latest_version", lambda: "9.9.9")
    monkeypatch.setattr("cartograph.update_check._current_version", lambda: "0.1.0")

    maybe_recommend_update(["search", "http"])

    assert stderr.getvalue() == ""


def test_update_recommendation_prints_when_forced(monkeypatch, tmp_path):
    from cartograph.update_check import maybe_recommend_update

    monkeypatch.setenv("CARTOGRAPH_FORCE_UPDATE_CHECK", "1")
    monkeypatch.setattr("cartograph.config._config_path",
                        lambda: str(tmp_path / "config.toml"))
    monkeypatch.setattr("cartograph.update_check._latest_version", lambda: "9.9.9")
    monkeypatch.setattr("cartograph.update_check._current_version", lambda: "0.1.0")

    stderr = io.StringIO()
    monkeypatch.setattr("sys.stderr", stderr)

    maybe_recommend_update(["search", "http"])

    text = stderr.getvalue()
    assert "Cartograph 9.9.9 is available" in text
    assert "cartograph config auto-update false" in text


def test_update_recommendation_respects_config(monkeypatch, tmp_path):
    from cartograph.config import set_value
    from cartograph.update_check import maybe_recommend_update

    monkeypatch.setenv("CARTOGRAPH_FORCE_UPDATE_CHECK", "1")
    monkeypatch.setattr("cartograph.config._config_path",
                        lambda: str(tmp_path / "config.toml"))
    set_value("auto-update", "false")
    monkeypatch.setattr("cartograph.update_check._latest_version", lambda: "9.9.9")
    monkeypatch.setattr("cartograph.update_check._current_version", lambda: "0.1.0")

    stderr = io.StringIO()
    monkeypatch.setattr("sys.stderr", stderr)

    maybe_recommend_update(["search", "http"])

    assert stderr.getvalue() == ""


def test_latest_version_uses_fresh_cache(monkeypatch, tmp_path):
    from cartograph.update_check import _latest_version

    cache = tmp_path / "update_check.json"
    cache.write_text(json.dumps({"checked_at": 1000, "latest_version": "2.0.0"}))
    monkeypatch.setattr("cartograph.update_check._cache_path", lambda: str(cache))
    monkeypatch.setattr("cartograph.update_check.time.time", lambda: 1001)

    def fail_fetch():
        raise AssertionError("fresh cache should avoid network")

    monkeypatch.setattr("cartograph.update_check._fetch_latest_version", fail_fetch)

    assert _latest_version() == "2.0.0"
    assert os.path.isfile(cache)


def test_latest_version_throttles_cached_failures(monkeypatch, tmp_path):
    from cartograph.update_check import _latest_version

    cache = tmp_path / "update_check.json"
    cache.write_text(json.dumps({"checked_at": 1000, "error": "offline"}))
    monkeypatch.setattr("cartograph.update_check._cache_path", lambda: str(cache))
    monkeypatch.setattr("cartograph.update_check.time.time", lambda: 1001)

    def fail_fetch():
        raise AssertionError("fresh failure cache should avoid network")

    monkeypatch.setattr("cartograph.update_check._fetch_latest_version", fail_fetch)

    assert _latest_version() is None
