"""Generate an OpenAPI 3.1 spec from a small set of endpoint metadata dicts."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.openapi_generator import build_openapi


def fetch(): pass
def create(): pass
def upload(): pass


endpoints = [
    {
        "method": "GET",
        "path": "/v1/things/{thing_id}",
        "function": fetch,
        "summary": "Fetch a thing",
        "tags": ["Things"],
        "response": "Thing",
        "responses": {200: "Thing", 404: "ErrorResponse"},
    },
    {
        "method": "POST",
        "path": "/v1/things",
        "function": create,
        "summary": "Create a thing",
        "tags": ["Things"],
        "request": "CreateThing",
        "response": "Thing",
    },
    {
        "method": "POST",
        "path": "/v1/things/{thing_id}/attachments",
        "function": upload,
        "tags": ["Things"],
        "multipart": {
            "fields": {"caption": "str"},
            "file_field": "file",
            "required": ["caption"],
        },
        "response": "Attachment",
    },
]

schemas = {
    "Thing": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
        "required": ["id"],
    },
    "CreateThing": {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    },
    "Attachment": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "caption": {"type": "string"}},
    },
    "ErrorResponse": {
        "type": "object",
        "properties": {"detail": {"type": "string"}},
    },
}

spec = build_openapi(
    endpoints,
    title="Example Things API",
    version="1.0.0",
    description="A small synthetic API to exercise the generator.",
    servers=[{"url": "https://api.example.com"}],
    schemas=schemas,
)

print(json.dumps(spec, indent=2))
