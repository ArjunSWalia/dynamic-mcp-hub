"""Convert OpenAPI specs to FastMCP tools."""

import re
from typing import Any, Callable
from urllib.parse import urljoin

import httpx
from fastmcp import FastMCP
from pydantic import create_model
from pydantic.fields import FieldInfo


class OpenAPIConversionError(Exception):
    """Raised when OpenAPI spec cannot be converted to MCP tools."""

    pass


# OpenAPI type to Python type mapping
OPENAPI_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def sanitize_tool_name(name: str) -> str:
    """
    Sanitize a string to be a valid tool name.

    Replaces non-alphanumeric characters with underscores.
    """
    # Replace {param} placeholders with just param
    name = re.sub(r"\{(\w+)\}", r"\1", name)
    # Replace non-alphanumeric (except underscore) with underscore
    name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    # Collapse multiple underscores
    name = re.sub(r"_+", "_", name)
    # Strip leading/trailing underscores
    name = name.strip("_")
    return name


def generate_tool_name(method: str, path: str, operation_id: str | None) -> str:
    """
    Generate a tool name for an operation.

    Uses operationId if available, otherwise METHOD__sanitized_path.
    """
    if operation_id:
        return sanitize_tool_name(operation_id)

    sanitized_path = sanitize_tool_name(path)
    return f"{method.upper()}__{sanitized_path}"


def openapi_type_to_python(schema: dict[str, Any]) -> type:
    """Convert an OpenAPI schema type to a Python type."""
    openapi_type = schema.get("type", "string")
    return OPENAPI_TYPE_MAP.get(openapi_type, Any)


def build_input_model(
    operation: dict[str, Any],
    path_params: list[dict[str, Any]],
    tool_name: str,
) -> type:
    """
    Build a Pydantic model for tool inputs from OpenAPI operation.

    Collects:
    - Path parameters (always required)
    - Query parameters
    - Request body (as 'body' field if JSON)
    """
    fields: dict[str, tuple[type, FieldInfo]] = {}

    # Collect parameters from operation and path-level
    all_params = path_params + operation.get("parameters", [])

    for param in all_params:
        param_name = param.get("name")
        if not param_name:
            continue

        param_in = param.get("in")
        if param_in not in ("path", "query"):
            continue  # Skip header/cookie params for MVP

        schema = param.get("schema", {})
        python_type = openapi_type_to_python(schema)

        # Path params are always required
        is_required = param_in == "path" or param.get("required", False)
        description = param.get("description", "")
        default = ... if is_required else None

        # Handle optional types
        if not is_required:
            python_type = python_type | None

        fields[param_name] = (python_type, FieldInfo(default=default, description=description))

    # Handle request body
    request_body = operation.get("requestBody", {})
    content = request_body.get("content", {})
    json_content = content.get("application/json", {})

    if json_content:
        body_required = request_body.get("required", False)
        body_description = request_body.get("description", "JSON request body")
        default = ... if body_required else None
        body_type = dict if body_required else dict | None

        fields["body"] = (body_type, FieldInfo(default=default, description=body_description))

    # Create the model dynamically
    model_name = f"{tool_name}Input"
    # For operations with no inputs, create an empty model
    return create_model(model_name, **fields)


def resolve_base_url(spec: dict[str, Any], base_url_override: str | None) -> str:
    """
    Resolve the base URL for API calls.

    Priority:
    1. base_url_override if provided
    2. First server URL from spec
    3. Raise error
    """
    if base_url_override:
        return base_url_override.rstrip("/")

    servers = spec.get("servers", [])
    if servers and isinstance(servers[0], dict) and "url" in servers[0]:
        return servers[0]["url"].rstrip("/")

    raise OpenAPIConversionError(
        "No server URL found in spec and no base_url_override provided"
    )


def build_url_with_path_params(base_url: str, path: str, params: dict[str, Any]) -> str:
    """
    Build the full URL, substituting path parameters.

    Example:
        base_url = "https://api.example.com"
        path = "/pets/{petId}"
        params = {"petId": 123}
        -> "https://api.example.com/pets/123"
    """
    # Substitute path parameters
    resolved_path = path
    for key, value in params.items():
        resolved_path = resolved_path.replace(f"{{{key}}}", str(value))

    # Join base URL and path
    if not base_url.endswith("/"):
        base_url += "/"
    if resolved_path.startswith("/"):
        resolved_path = resolved_path[1:]

    return urljoin(base_url, resolved_path)


def create_tool_handler(
    method: str,
    path: str,
    operation: dict[str, Any],
    path_params_names: set[str],
    query_param_names: set[str],
    has_body: bool,
    base_url: str,
) -> Callable:
    """
    Create an async handler function for an OpenAPI operation.

    The handler:
    1. Extracts path params, query params, and body from input
    2. Builds the request URL
    3. Makes the HTTP call
    4. Returns JSON or text response
    """

    async def handler(**kwargs: Any) -> dict[str, Any] | str:
        # Separate params by type
        path_params = {k: v for k, v in kwargs.items() if k in path_params_names}
        query_params = {
            k: v for k, v in kwargs.items() if k in query_param_names and v is not None
        }
        body = kwargs.get("body") if has_body else None

        # Build URL
        url = build_url_with_path_params(base_url, path, path_params)

        # Make request
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method=method.upper(),
                url=url,
                params=query_params if query_params else None,
                json=body if body else None,
            )

        # Parse response
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                return response.json()
            except Exception:
                pass

        # Return as structured text response
        return {
            "text": response.text,
            "status_code": response.status_code,
            "content_type": content_type,
        }

    return handler


def make_tool_function(
    handler: Callable,
    input_model: type,
    tool_name: str,
    description: str,
) -> Callable:
    """
    Create a tool function with properly typed signature for FastMCP.

    FastMCP doesn't support **kwargs, so we create a function that takes
    a Pydantic model as its single argument.
    """

    async def tool_fn(params: input_model) -> dict[str, Any] | str:  # type: ignore
        # Convert Pydantic model to dict for the handler
        params_dict = params.model_dump(exclude_none=True)
        return await handler(**params_dict)

    # Set metadata
    tool_fn.__name__ = tool_name
    tool_fn.__doc__ = description

    return tool_fn


def build_mcp_server(
    spec_name: str,
    spec: dict[str, Any],
    base_url_override: str | None = None,
) -> tuple[FastMCP, list[str]]:
    """
    Build a FastMCP server from an OpenAPI spec.

    Args:
        spec_name: Name for the MCP server
        spec: Parsed OpenAPI spec dict
        base_url_override: Optional override for base URL

    Returns:
        Tuple of (FastMCP server, list of tool names)

    Raises:
        OpenAPIConversionError: If conversion fails
    """
    base_url = resolve_base_url(spec, base_url_override)
    mcp = FastMCP(spec_name)
    tool_names: list[str] = []

    paths = spec.get("paths", {})
    http_methods = {"get", "post", "put", "patch", "delete", "options", "head"}

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue

        # Path-level parameters apply to all operations
        path_level_params = path_item.get("parameters", [])

        for method in http_methods:
            if method not in path_item:
                continue

            operation = path_item[method]
            if not isinstance(operation, dict):
                continue

            # Generate tool name
            operation_id = operation.get("operationId")
            tool_name = generate_tool_name(method, path, operation_id)
            tool_names.append(tool_name)

            # Build input model
            input_model = build_input_model(operation, path_level_params, tool_name)

            # Collect param names by type for the handler
            all_params = path_level_params + operation.get("parameters", [])
            path_param_names = {
                p["name"] for p in all_params if p.get("in") == "path" and "name" in p
            }
            query_param_names = {
                p["name"] for p in all_params if p.get("in") == "query" and "name" in p
            }

            # Check if operation has JSON body
            request_body = operation.get("requestBody", {})
            content = request_body.get("content", {})
            has_body = "application/json" in content

            # Create handler
            handler = create_tool_handler(
                method=method,
                path=path,
                operation=operation,
                path_params_names=path_param_names,
                query_param_names=query_param_names,
                has_body=has_body,
                base_url=base_url,
            )

            # Get description
            description = operation.get("summary") or operation.get("description") or ""

            # Create tool function with proper signature
            tool_fn = make_tool_function(handler, input_model, tool_name, description)

            # Register with FastMCP
            mcp.tool(name=tool_name, description=description)(tool_fn)

    return mcp, tool_names


def build_mcp_http_app(
    spec_name: str,
    spec: dict[str, Any],
    base_url_override: str | None = None,
) -> tuple[Callable, list[str]]:
    """
    Build an MCP HTTP app from an OpenAPI spec.

    Args:
        spec_name: Name for the MCP server
        spec: Parsed OpenAPI spec dict
        base_url_override: Optional override for base URL

    Returns:
        Tuple of (ASGI app, list of tool names)
    """
    mcp, tool_names = build_mcp_server(spec_name, spec, base_url_override)
    return mcp.http_app(), tool_names
