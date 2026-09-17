import pytest

from src import build_openapi


def _ep(method, path, function=None, **extras):
    """Build an endpoint metadata dict like the registry would produce."""
    meta = {"method": method, "path": path, **extras}
    if function is not None:
        meta["function"] = function
    return meta


# ---------- spec skeleton ----------

def test_spec_has_required_top_level_keys():
    spec = build_openapi([], title="API", version="1.0")
    assert spec["openapi"] == "3.1.0"
    assert spec["info"] == {"title": "API", "version": "1.0"}
    assert spec["paths"] == {}
    assert "components" not in spec
    assert "servers" not in spec


def test_description_added_to_info_when_provided():
    spec = build_openapi([], title="API", version="1.0", description="hello")
    assert spec["info"]["description"] == "hello"


def test_servers_and_schemas_optional_sections():
    spec = build_openapi(
        [], title="API", version="1.0",
        servers=[{"url": "https://api.example.com"}],
        schemas={"X": {"type": "object"}},
    )
    assert spec["servers"] == [{"url": "https://api.example.com"}]
    assert spec["components"]["schemas"] == {"X": {"type": "object"}}


def test_missing_title_rejected():
    with pytest.raises(ValueError):
        build_openapi([], title="", version="1.0")


def test_missing_version_rejected():
    with pytest.raises(ValueError):
        build_openapi([], title="A", version="")


def test_endpoints_non_list_rejected():
    with pytest.raises(TypeError):
        build_openapi("not a list", title="A", version="1")


# ---------- paths + operations ----------

def test_simple_get_endpoint():
    spec = build_openapi(
        [_ep("GET", "/v1/things")],
        title="API", version="1.0",
    )
    op = spec["paths"]["/v1/things"]["get"]
    assert op["responses"] == {"200": {"description": "Successful response"}}
    assert "parameters" not in op
    assert "requestBody" not in op


def test_post_default_success_is_201():
    spec = build_openapi(
        [_ep("POST", "/v1/things")],
        title="API", version="1.0",
    )
    assert "201" in spec["paths"]["/v1/things"]["post"]["responses"]


def test_method_lowered_in_path_object():
    spec = build_openapi(
        [_ep("PATCH", "/v1/x")],
        title="API", version="1.0",
    )
    assert "patch" in spec["paths"]["/v1/x"]
    assert "PATCH" not in spec["paths"]["/v1/x"]


def test_multiple_methods_same_path():
    spec = build_openapi(
        [_ep("GET", "/v1/x"), _ep("DELETE", "/v1/x")],
        title="API", version="1.0",
    )
    path_obj = spec["paths"]["/v1/x"]
    assert "get" in path_obj
    assert "delete" in path_obj


def test_summary_description_tags_passed_through():
    spec = build_openapi(
        [_ep("GET", "/v1/x", summary="short", description="long",
             tags=["read", "demo"])],
        title="A", version="1",
    )
    op = spec["paths"]["/v1/x"]["get"]
    assert op["summary"] == "short"
    assert op["description"] == "long"
    assert op["tags"] == ["read", "demo"]


def test_operation_id_from_function_name():
    def my_op(): pass
    spec = build_openapi(
        [_ep("GET", "/v1/x", function=my_op)],
        title="A", version="1",
    )
    assert spec["paths"]["/v1/x"]["get"]["operationId"] == "my_op"


def test_explicit_operation_id_wins():
    def my_op(): pass
    spec = build_openapi(
        [_ep("GET", "/v1/x", function=my_op, operation_id="custom")],
        title="A", version="1",
    )
    assert spec["paths"]["/v1/x"]["get"]["operationId"] == "custom"


def test_endpoint_missing_method_rejected():
    with pytest.raises(ValueError):
        build_openapi([{"path": "/x"}], title="A", version="1")


def test_endpoint_missing_path_rejected():
    with pytest.raises(ValueError):
        build_openapi([{"method": "GET"}], title="A", version="1")


# ---------- path parameters ----------

def test_path_parameters_extracted():
    spec = build_openapi(
        [_ep("GET", "/v1/widgets/{owner}/{widget_id}")],
        title="A", version="1",
    )
    params = spec["paths"]["/v1/widgets/{owner}/{widget_id}"]["get"]["parameters"]
    names = [p["name"] for p in params]
    assert names == ["owner", "widget_id"]
    assert all(p["in"] == "path" for p in params)
    assert all(p["required"] is True for p in params)


def test_no_path_parameters_means_no_parameters_key():
    spec = build_openapi(
        [_ep("GET", "/v1/things")],
        title="A", version="1",
    )
    assert "parameters" not in spec["paths"]["/v1/things"]["get"]


# ---------- request body: JSON ----------

def test_json_request_emits_ref():
    spec = build_openapi(
        [_ep("POST", "/v1/x", request="CreateX")],
        title="A", version="1",
    )
    body = spec["paths"]["/v1/x"]["post"]["requestBody"]
    assert body["required"] is True
    schema = body["content"]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/CreateX"}


# ---------- request body: multipart ----------

def test_multipart_as_ref_string():
    spec = build_openapi(
        [_ep("POST", "/v1/upload", multipart="UploadForm")],
        title="A", version="1",
    )
    body = spec["paths"]["/v1/upload"]["post"]["requestBody"]
    schema = body["content"]["multipart/form-data"]["schema"]
    assert schema == {"$ref": "#/components/schemas/UploadForm"}


def test_multipart_as_inline_dict():
    spec = build_openapi(
        [_ep("POST", "/v1/upload", multipart={
            "fields": {"widget_id": "str", "count": "int", "open": "bool"},
            "file_field": "file",
            "required": ["widget_id"],
        })],
        title="A", version="1",
    )
    schema = spec["paths"]["/v1/upload"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert schema["type"] == "object"
    assert schema["properties"]["widget_id"] == {"type": "string"}
    assert schema["properties"]["count"] == {"type": "integer"}
    assert schema["properties"]["open"] == {"type": "boolean"}
    assert schema["properties"]["file"] == {"type": "string", "format": "binary"}
    assert schema["required"] == ["widget_id"]


def test_multipart_invalid_type_rejected():
    with pytest.raises(TypeError):
        build_openapi(
            [_ep("POST", "/v1/u", multipart=42)],
            title="A", version="1",
        )


# ---------- responses ----------

def test_response_name_becomes_200_ref():
    spec = build_openapi(
        [_ep("GET", "/v1/x", response="Thing")],
        title="A", version="1",
    )
    resp = spec["paths"]["/v1/x"]["get"]["responses"]["200"]
    assert resp["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/Thing"}


def test_multiple_responses():
    spec = build_openapi(
        [_ep("GET", "/v1/x", responses={200: "Thing", 404: "Error"})],
        title="A", version="1",
    )
    responses = spec["paths"]["/v1/x"]["get"]["responses"]
    assert set(responses.keys()) == {"200", "404"}
    assert responses["404"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/Error"}


def test_response_can_be_full_dict():
    custom = {
        "description": "No content",
        "headers": {"X-Trace": {"schema": {"type": "string"}}},
    }
    spec = build_openapi(
        [_ep("DELETE", "/v1/x", responses={204: custom})],
        title="A", version="1",
    )
    assert spec["paths"]["/v1/x"]["delete"]["responses"]["204"] == custom


def test_response_invalid_type_rejected():
    with pytest.raises(TypeError):
        build_openapi(
            [_ep("GET", "/v1/x", response=42)],
            title="A", version="1",
        )


# ---------- integration shape ----------

def test_integrated_spec_shape():
    def push(): pass
    def inspect(): pass
    spec = build_openapi(
        [
            _ep("GET", "/v1/widgets/{owner}/{widget_id}", function=inspect,
                tags=["Widgets"], response="InspectResponse"),
            _ep("POST", "/v1/widgets/{widget_id}/publish", function=push,
                tags=["Widgets"],
                multipart={"fields": {"widget_id": "str"}, "file_field": "file"},
                response="PushResponse"),
        ],
        title="Cartograph Registry",
        version="1.0.0",
        description="Widget registry API",
        servers=[{"url": "https://registry.example.com"}],
        schemas={
            "InspectResponse": {"type": "object", "properties": {"id": {"type": "string"}}},
            "PushResponse": {"type": "object", "properties": {"status": {"type": "string"}}},
        },
    )
    assert spec["info"]["description"] == "Widget registry API"
    assert spec["servers"][0]["url"] == "https://registry.example.com"
    assert "InspectResponse" in spec["components"]["schemas"]
    assert "PushResponse" in spec["components"]["schemas"]
    assert len(spec["paths"]) == 2
    inspect_op = spec["paths"]["/v1/widgets/{owner}/{widget_id}"]["get"]
    assert inspect_op["operationId"] == "inspect"
    assert inspect_op["tags"] == ["Widgets"]
    assert len(inspect_op["parameters"]) == 2
