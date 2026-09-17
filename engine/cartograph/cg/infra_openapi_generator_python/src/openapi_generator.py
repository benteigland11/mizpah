"""Build an OpenAPI 3.1 spec dict from endpoint metadata.

Consumes the dict shape produced by a declarative HTTP endpoint registry:

    {
        "method": "POST",
        "path": "/v1/widgets/{widget_id}/publish",
        "function": <callable>,
        "summary": "Publish a widget",                 # optional
        "description": "...",                           # optional
        "tags": ["Widgets"],                            # optional
        "request": "PublishRequest",                    # optional, JSON body
        "multipart": {                                  # optional, form body
            "fields": {"widget_id": "str", "stamp": "str"},
            "file_field": "file",
            "file_content_type": "application/zip",
        },
        "response": "PublishResponse",                  # optional, 200 body
        "responses": {200: "PublishResponse", 404: "ErrorResponse"},  # or many
    }

The generator does not introspect Python types. It emits $ref pointers to
named schemas (#/components/schemas/<name>) and expects the caller to pass
those schemas in via schemas=. This keeps the widget focused on OpenAPI
structure, not Python-to-JSON-Schema conversion.

Output is a plain dict — the caller serializes to JSON, YAML, or stores it
directly.
"""

import re
from typing import Any


_PATH_PARAM_RE = re.compile(r"\{([^}]+)\}")
_DEFAULT_SUCCESS = {
    "GET": 200,
    "POST": 201,
    "PUT": 200,
    "PATCH": 200,
    "DELETE": 200,
    "HEAD": 200,
    "OPTIONS": 200,
}


def build_openapi(
    endpoints: list[dict],
    *,
    title: str,
    version: str,
    description: str = "",
    servers: list[dict] | None = None,
    schemas: dict[str, dict] | None = None,
    openapi_version: str = "3.1.0",
) -> dict:
    """Return an OpenAPI 3.1 spec dict built from endpoints metadata.

    endpoints is the list produced by a compatible registry
    (e.g. get_endpoints(module) from infra-http-endpoint-registry-python).

    schemas is a mapping of schema name -> JSON Schema dict, placed under
    components.schemas. Endpoint metadata that references a schema name
    (as request, response, or a value in responses) is emitted as a $ref;
    the caller is responsible for providing the schemas themselves. Missing
    schemas are NOT validated — the ref is emitted regardless so callers
    can assemble the schema dict incrementally.
    """
    if not isinstance(endpoints, list):
        raise TypeError("endpoints must be a list of metadata dicts")
    if not title or not isinstance(title, str):
        raise ValueError("title is required")
    if not version or not isinstance(version, str):
        raise ValueError("version is required")

    spec: dict[str, Any] = {
        "openapi": openapi_version,
        "info": _build_info(title, version, description),
        "paths": _build_paths(endpoints),
    }
    if servers:
        spec["servers"] = list(servers)
    if schemas:
        spec["components"] = {"schemas": dict(schemas)}
    return spec


def _build_info(title: str, version: str, description: str) -> dict:
    info: dict[str, Any] = {"title": title, "version": version}
    if description:
        info["description"] = description
    return info


def _build_paths(endpoints: list[dict]) -> dict:
    paths: dict[str, dict] = {}
    for ep in endpoints:
        if not isinstance(ep, dict):
            raise TypeError("each endpoint must be a dict")
        method = ep.get("method")
        path = ep.get("path")
        if not method or not path:
            raise ValueError(f"endpoint missing method/path: {ep!r}")
        op = _build_operation(ep)
        paths.setdefault(path, {})[method.lower()] = op
    return paths


def _build_operation(ep: dict) -> dict:
    op: dict[str, Any] = {}

    if ep.get("summary"):
        op["summary"] = ep["summary"]
    if ep.get("description"):
        op["description"] = ep["description"]
    if ep.get("tags"):
        op["tags"] = list(ep["tags"])

    operation_id = ep.get("operation_id")
    if not operation_id:
        fn = ep.get("function")
        if fn is not None and hasattr(fn, "__name__"):
            operation_id = fn.__name__
    if operation_id:
        op["operationId"] = operation_id

    params = _path_parameters(ep["path"])
    if params:
        op["parameters"] = params

    body = _build_request_body(ep)
    if body:
        op["requestBody"] = body

    op["responses"] = _build_responses(ep)
    return op


def _path_parameters(path: str) -> list[dict]:
    names = _PATH_PARAM_RE.findall(path)
    return [
        {
            "name": name,
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
        }
        for name in names
    ]


def _build_request_body(ep: dict) -> dict | None:
    multipart = ep.get("multipart")
    if multipart:
        return _multipart_body(multipart)
    request = ep.get("request")
    if request:
        return {
            "required": True,
            "content": {
                "application/json": {"schema": _ref(request)},
            },
        }
    return None


def _multipart_body(multipart: Any) -> dict:
    """Accept either a schema name (string) or a structured dict.

    String form: "MyMultipartSchema" -> emits a $ref directly.
    Dict form:   {"fields": {...}, "file_field": "file",
                  "file_content_type": "application/zip"}
                 -> inline schema describing the form parts.
    """
    if isinstance(multipart, str):
        schema: dict[str, Any] = _ref(multipart)
    elif isinstance(multipart, dict):
        properties: dict[str, Any] = {}
        for field_name, field_type in (multipart.get("fields") or {}).items():
            properties[field_name] = _type_to_schema(field_type)
        file_field = multipart.get("file_field")
        if file_field:
            properties[file_field] = {
                "type": "string",
                "format": "binary",
            }
        schema = {"type": "object", "properties": properties}
        required = list(multipart.get("required") or [])
        if required:
            schema["required"] = required
    else:
        raise TypeError(f"multipart must be str or dict, got {type(multipart).__name__}")

    content_type = "multipart/form-data"
    return {
        "required": True,
        "content": {content_type: {"schema": schema}},
    }


def _build_responses(ep: dict) -> dict:
    if "responses" in ep and ep["responses"]:
        return {
            str(code): _response_entry(value)
            for code, value in ep["responses"].items()
        }
    response = ep.get("response")
    success_code = _DEFAULT_SUCCESS.get(ep["method"].upper(), 200)
    if response:
        return {str(success_code): _response_entry(response)}
    return {str(success_code): {"description": "Successful response"}}


def _response_entry(value: Any) -> dict:
    """Accept a schema name (str) or a full response dict."""
    if isinstance(value, str):
        return {
            "description": "Successful response",
            "content": {"application/json": {"schema": _ref(value)}},
        }
    if isinstance(value, dict):
        return value
    raise TypeError(f"response must be str or dict, got {type(value).__name__}")


def _ref(schema_name: str) -> dict:
    return {"$ref": f"#/components/schemas/{schema_name}"}


def _type_to_schema(type_hint: str) -> dict:
    """Minimal Python-type-name to JSON Schema mapping for multipart fields."""
    mapping = {
        "str": {"type": "string"},
        "string": {"type": "string"},
        "int": {"type": "integer"},
        "integer": {"type": "integer"},
        "float": {"type": "number"},
        "number": {"type": "number"},
        "bool": {"type": "boolean"},
        "boolean": {"type": "boolean"},
    }
    return mapping.get(type_hint, {"type": "string"})
