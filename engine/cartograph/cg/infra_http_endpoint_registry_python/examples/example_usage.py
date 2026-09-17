"""Declare a tiny client surface and introspect it with get_endpoints."""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.http_endpoint_registry import endpoint, get_endpoints


# Build an ad-hoc module holding the decorated client functions.
client = types.ModuleType("demo_client")


@endpoint("GET", "/v1/widgets/{owner}/{widget_id}", response="InspectResponse")
def inspect(owner, widget_id):
    """Fetch one widget."""
    return {"owner": owner, "widget_id": widget_id}


@endpoint("POST", "/v1/widgets/{widget_id}/publish",
          response="PushResponse", multipart="PushFields",
          summary="Publish a new widget version")
def push(widget_id):
    return {"published": widget_id}


@endpoint("DELETE", "/v1/widgets/{widget_id}")
def delete(widget_id):
    return {"deleted": widget_id}


client.inspect = inspect
client.push = push
client.delete = delete


# Consumers walk the registry to emit docs, OpenAPI, test fixtures, etc.
print("endpoints in demo_client:")
for ep in get_endpoints(client):
    method = ep["method"]
    path = ep["path"]
    name = ep["function"].__name__
    extras = {k: v for k, v in ep.items() if k not in {"method", "path", "function"}}
    print(f"  {method:6} {path:50} -> {name}  {extras}")

print("\nFunctions are still callable:")
print(f"  inspect: {inspect('@alice', 'demo')}")
print(f"  push:    {push('demo')}")
