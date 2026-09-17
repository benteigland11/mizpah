from src.resource_ref_resolve import (
    ResolveFacts,
    classify_token,
    parse_cloud_ref,
    resolve_module_ref,
)


def test_classify_token() -> None:
    assert classify_token(None) == "empty"
    assert classify_token("") == "empty"
    assert classify_token("@alice/foo-python") == "cloud_ref"
    assert classify_token("cg/foo_python") == "path_like"
    assert classify_token(".") == "path_like"
    assert classify_token("backend-retry-python") == "bare_id"


def test_parse_cloud_ref() -> None:
    o, b, e = parse_cloud_ref("@alice/backend-retry-python")
    assert o == "alice" and b == "backend-retry-python" and e is None
    assert parse_cloud_ref("nope")[2]


def test_lib_lookup() -> None:
    r = resolve_module_ref(
        "backend-retry-python",
        lib=True,
        facts=ResolveFacts(
            lib_path="/lib/backend-retry-python",
            lib_kind="widget",
            token_manifest_id="backend-retry-python",
        ),
    )
    assert r.ok and r.via == "lib"
    assert r.path == "/lib/backend-retry-python"
    assert r.id == "backend-retry-python"


def test_lib_rejects_path_token() -> None:
    r = resolve_module_ref("cg/foo", lib=True, facts=ResolveFacts())
    assert not r.ok and "directory" in (r.error or "")


def test_directory_token_prefers_manifest_id() -> None:
    """Regression: path string must not become the cloud id."""
    r = resolve_module_ref(
        "cg/backend_retry_python",
        path=".",
        facts=ResolveFacts(
            token_dir="/proj/cg/backend_retry_python",
            token_manifest_id="backend-retry-python",
        ),
    )
    assert r.ok and r.via == "dir"
    assert r.id == "backend-retry-python"
    assert r.path == "/proj/cg/backend_retry_python"
    assert r.id != "cg/backend_retry_python"


def test_bare_id_library() -> None:
    r = resolve_module_ref(
        "backend-retry-python",
        facts=ResolveFacts(
            lib_path="/lib/backend-retry-python",
            token_manifest_id="backend-retry-python",
        ),
    )
    assert r.ok and r.via == "id"


def test_cwd_default() -> None:
    r = resolve_module_ref(
        None,
        path=".",
        facts=ResolveFacts(
            cwd_dir="/proj/cg/widget",
            cwd_manifest_id="backend-retry-python",
        ),
    )
    assert r.ok and r.via == "cwd"
    assert r.id == "backend-retry-python"


def test_cloud_ref() -> None:
    r = resolve_module_ref("@alice/backend-retry-python")
    assert r.ok and r.is_cloud and r.id == "backend-retry-python"


def test_missing_id() -> None:
    r = resolve_module_ref("no-such-widget", facts=ResolveFacts())
    assert not r.ok
