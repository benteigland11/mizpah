"""Tests for blueprint identity and schema validation."""

import pytest

from cartograph.blueprints import (
    BlueprintId,
    compose_blueprint_id,
    derive_domains,
    is_blueprint_id,
    parse_blueprint_id,
    validate_blueprint_manifest,
)


# --- ID parsing -----------------------------------------------------------


def test_is_blueprint_id_true_cases():
    assert is_blueprint_id("bp-foo-python")
    assert is_blueprint_id("bp-auth-flow-python")
    assert is_blueprint_id("cg-bp-foo-python")
    assert is_blueprint_id("myorg-bp-auth-flow-python")


def test_is_blueprint_id_false_cases():
    assert not is_blueprint_id("")
    assert not is_blueprint_id("backend-foo-python")
    assert not is_blueprint_id("bp")
    assert not is_blueprint_id("cg-foo-python")


def test_parse_local_blueprint_id():
    bid = parse_blueprint_id("bp-auth-flow-python")
    assert bid.registry is None
    assert bid.name == "auth-flow"
    assert bid.language == "python"
    assert bid.compose() == "bp-auth-flow-python"


def test_parse_published_blueprint_id():
    bid = parse_blueprint_id("cg-bp-auth-flow-python")
    assert bid.registry == "cg"
    assert bid.name == "auth-flow"
    assert bid.language == "python"
    assert bid.compose() == "cg-bp-auth-flow-python"


def test_parse_private_registry_blueprint_id():
    bid = parse_blueprint_id("myorg-bp-auth-flow-python")
    assert bid.registry == "myorg"
    assert bid.name == "auth-flow"
    assert bid.language == "python"


def test_parse_single_token_name():
    bid = parse_blueprint_id("bp-auth-python")
    assert bid.name == "auth"


def test_parse_rejects_missing_bp_marker():
    with pytest.raises(ValueError):
        parse_blueprint_id("backend-foo-python")


def test_parse_rejects_too_few_parts():
    with pytest.raises(ValueError):
        parse_blueprint_id("bp-python")


def test_parse_rejects_empty_name():
    # 'cg-bp--python' splits to ['cg', 'bp', '', 'python']
    with pytest.raises(ValueError):
        parse_blueprint_id("cg-bp--python")


def test_parse_rejects_bad_name_chars():
    with pytest.raises(ValueError):
        parse_blueprint_id("bp-Auth_Flow-python")


def test_parse_empty_string():
    with pytest.raises(ValueError):
        parse_blueprint_id("")


# --- ID composition --------------------------------------------------------


def test_compose_local():
    assert compose_blueprint_id("auth-flow", "python") == "bp-auth-flow-python"


def test_compose_with_registry():
    assert (
        compose_blueprint_id("auth-flow", "python", registry="cg")
        == "cg-bp-auth-flow-python"
    )


def test_compose_lowercases_language():
    assert compose_blueprint_id("foo", "Python") == "bp-foo-python"


def test_compose_rejects_bad_name():
    with pytest.raises(ValueError):
        compose_blueprint_id("Auth_Flow", "python")


def test_compose_rejects_empty_language():
    with pytest.raises(ValueError):
        compose_blueprint_id("foo", "")


# --- Schema validation -----------------------------------------------------


def _good_manifest():
    return {
        "id": "bp-auth-flow-python",
        "name": "auth-flow",
        "language": "python",
        "version": "0.1.0",
        "description": "Composed auth flow",
        "tags": ["auth", "session"],
        "dependencies": [
            {"id": "backend-rate-limit-python", "version": "1.4.0"},
            {"id": "security-audit-log-python", "version": "1.0.1"},
        ],
    }


def _validate(manifest, **kw):
    kw.setdefault("supported_languages", {"python", "javascript"})
    kw.setdefault("valid_domains", {"backend", "security", "frontend"})
    kw.setdefault(
        "library_widget_ids",
        {"backend-rate-limit-python", "security-audit-log-python"},
    )
    return validate_blueprint_manifest(manifest, **kw)


def test_validate_happy_path():
    res = _validate(_good_manifest())
    assert res["ok"], res["errors"]
    assert res["errors"] == []


def test_validate_missing_field():
    m = _good_manifest()
    del m["description"]
    res = _validate(m)
    assert not res["ok"]
    assert any("description" in e for e in res["errors"])


def test_validate_id_name_mismatch():
    m = _good_manifest()
    m["name"] = "different-name"
    res = _validate(m)
    assert not res["ok"]
    assert any("name" in e.lower() for e in res["errors"])


def test_validate_id_language_mismatch():
    m = _good_manifest()
    m["language"] = "javascript"
    res = _validate(m)
    assert not res["ok"]
    assert any("language" in e.lower() for e in res["errors"])


def test_validate_local_id_with_registry_prefix_rejected():
    m = _good_manifest()
    m["id"] = "cg-bp-auth-flow-python"
    res = _validate(m)
    assert not res["ok"]
    assert any("registry prefix" in e.lower() for e in res["errors"])


def test_validate_unsupported_language():
    m = _good_manifest()
    m["id"] = "bp-foo-rust"
    m["name"] = "foo"
    m["language"] = "rust"
    m["dependencies"] = [{"id": "backend-foo-rust", "version": "1.0.0"}]
    res = _validate(m, library_widget_ids={"backend-foo-rust"})
    assert not res["ok"]
    assert any("rust" in e.lower() for e in res["errors"])


def test_validate_bad_version():
    m = _good_manifest()
    m["version"] = "v1"
    res = _validate(m)
    assert not res["ok"]
    assert any("semver" in e.lower() for e in res["errors"])


def test_validate_todo_in_description():
    m = _good_manifest()
    m["description"] = "[TODO] fill me in"
    res = _validate(m)
    assert not res["ok"]
    assert any("TODO" in e for e in res["errors"])


def test_validate_empty_tags():
    m = _good_manifest()
    m["tags"] = []
    res = _validate(m)
    assert not res["ok"]
    assert any("tags" in e.lower() for e in res["errors"])


def test_validate_todo_tag():
    m = _good_manifest()
    m["tags"] = ["[TODO: add 3-5 tags]"]
    res = _validate(m)
    assert not res["ok"]


def test_validate_empty_deps():
    m = _good_manifest()
    m["dependencies"] = []
    res = _validate(m)
    assert not res["ok"]
    assert any("dependencies" in e.lower() for e in res["errors"])


def test_validate_dep_self_reference():
    m = _good_manifest()
    m["dependencies"].append({"id": "bp-auth-flow-python", "version": "0.1.0"})
    res = _validate(m, library_widget_ids={
        "backend-rate-limit-python", "security-audit-log-python", "bp-auth-flow-python"
    })
    assert not res["ok"]
    # Two errors expected: self-reference + leaf-only violation
    joined = " ".join(res["errors"]).lower()
    assert "self-reference" in joined


def test_validate_leaf_only_blueprint_dep():
    m = _good_manifest()
    m["dependencies"] = [{"id": "bp-other-python", "version": "0.1.0"}]
    res = _validate(m, library_widget_ids={"bp-other-python"})
    assert not res["ok"]
    assert any("leaf-only" in e.lower() for e in res["errors"])


def test_validate_dep_version_range_rejected():
    m = _good_manifest()
    m["dependencies"][0]["version"] = ">=1.0.0"
    res = _validate(m)
    assert not res["ok"]
    assert any("semver" in e.lower() for e in res["errors"])


def test_validate_dep_duplicate():
    m = _good_manifest()
    m["dependencies"].append(
        {"id": "backend-rate-limit-python", "version": "1.4.0"}
    )
    res = _validate(m)
    assert not res["ok"]
    assert any("duplicat" in e.lower() for e in res["errors"])


def test_validate_dep_cross_language_rejected():
    m = _good_manifest()
    m["dependencies"].append(
        {"id": "frontend-button-javascript", "version": "1.0.0"}
    )
    res = _validate(
        m,
        library_widget_ids={
            "backend-rate-limit-python",
            "security-audit-log-python",
            "frontend-button-javascript",
        },
    )
    assert not res["ok"]
    assert any("single language" in e.lower() or "single-language" in e.lower()
               for e in res["errors"])


def test_validate_dep_not_in_project_cg():
    m = _good_manifest()
    res = _validate(m, library_widget_ids={"backend-rate-limit-python"})
    assert not res["ok"]
    # The schema check is now strictly project-cg/, not local library.
    assert any("cg/" in e for e in res["errors"])


def test_validate_skips_library_check_when_not_provided():
    m = _good_manifest()
    res = validate_blueprint_manifest(
        m,
        supported_languages={"python"},
        valid_domains={"backend", "security"},
    )
    assert res["ok"], res["errors"]


def test_validate_optional_domains_field():
    m = _good_manifest()
    m["domains"] = ["backend", "security"]
    res = _validate(m)
    assert res["ok"], res["errors"]


def test_validate_invalid_domain():
    m = _good_manifest()
    m["domains"] = ["not-a-domain"]
    res = _validate(m)
    assert not res["ok"]
    assert any("domain" in e.lower() for e in res["errors"])


# --- derive_domains --------------------------------------------------------


def test_derive_domains_unions_and_sorts():
    deps = [
        {"id": "a-python", "version": "1.0.0"},
        {"id": "b-python", "version": "1.0.0"},
        {"id": "c-python", "version": "1.0.0"},
    ]
    library = {
        "a-python": {"meta": {"domain": "security"}},
        "b-python": {"meta": {"domain": "backend"}},
        "c-python": {"meta": {"domain": "backend"}},  # dup, dedup expected
    }
    domains = derive_domains(deps, lookup_widget=lambda i: library.get(i))
    assert domains == ["backend", "security"]


def test_derive_domains_skips_unknown_deps():
    deps = [{"id": "missing-python", "version": "1.0.0"}]
    domains = derive_domains(deps, lookup_widget=lambda i: None)
    assert domains == []
