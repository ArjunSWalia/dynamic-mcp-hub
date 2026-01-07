"""Load and validate OpenAPI specs from various sources."""

import json
from typing import Any

import yaml


class OpenAPILoadError(Exception):
    """Raised when an OpenAPI spec cannot be loaded or parsed."""

    pass


class OpenAPIValidationError(Exception):
    """Raised when an OpenAPI spec fails validation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"Validation failed: {'; '.join(errors)}")


def detect_and_parse(content: str | bytes) -> dict[str, Any]:
    """
    Detect whether content is YAML or JSON and parse accordingly.

    Args:
        content: Raw spec content as string or bytes

    Returns:
        Parsed spec as dict

    Raises:
        OpenAPILoadError: If content cannot be parsed as YAML or JSON
    """
    if isinstance(content, bytes):
        content = content.decode("utf-8")

    content = content.strip()

    # Try JSON first (faster and unambiguous if it starts with '{')
    if content.startswith("{"):
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise OpenAPILoadError(f"Invalid JSON: {e}")

    # Try YAML (which is a superset of JSON, so this handles both)
    try:
        result = yaml.safe_load(content)
        if not isinstance(result, dict):
            raise OpenAPILoadError(f"Spec must be an object, got {type(result).__name__}")
        return result
    except yaml.YAMLError as e:
        raise OpenAPILoadError(f"Invalid YAML: {e}")


def validate_openapi_spec(spec: dict[str, Any]) -> list[str]:
    """
    Perform minimal validation on an OpenAPI spec.

    MVP validation rules:
    - Must have 'openapi' field (string)
    - Must have 'paths' field (object)
    - 'paths' should not be empty (warning, not error)

    Args:
        spec: Parsed OpenAPI spec

    Returns:
        List of validation error messages (empty if valid)
    """
    errors: list[str] = []

    # Check for 'openapi' version field
    if "openapi" not in spec:
        errors.append("Missing required field 'openapi'")
    elif not isinstance(spec["openapi"], str):
        errors.append("Field 'openapi' must be a string")
    else:
        version = spec["openapi"]
        if not version.startswith("3."):
            errors.append(f"Only OpenAPI 3.x is supported, got '{version}'")

    # Check for 'paths' field
    if "paths" not in spec:
        errors.append("Missing required field 'paths'")
    elif not isinstance(spec["paths"], dict):
        errors.append("Field 'paths' must be an object")
    elif len(spec["paths"]) == 0:
        # This is a warning, not an error - still valid but useless
        pass

    return errors


def load_from_string(content: str | bytes) -> tuple[dict[str, Any], list[str]]:
    """
    Load and validate an OpenAPI spec from a string.

    Args:
        content: Raw spec content

    Returns:
        Tuple of (parsed_spec, validation_errors)

    Raises:
        OpenAPILoadError: If content cannot be parsed
    """
    spec = detect_and_parse(content)
    errors = validate_openapi_spec(spec)
    return spec, errors


def get_spec_info(spec: dict[str, Any]) -> dict[str, Any]:
    """
    Extract basic info from a spec for display purposes.

    Returns:
        Dict with title, version, description, server_urls, path_count, operation_count
    """
    info = spec.get("info", {})
    servers = spec.get("servers", [])
    paths = spec.get("paths", {})

    # Count operations
    operation_count = 0
    http_methods = {"get", "post", "put", "patch", "delete", "options", "head"}
    for path_item in paths.values():
        if isinstance(path_item, dict):
            for method in http_methods:
                if method in path_item:
                    operation_count += 1

    return {
        "title": info.get("title", "Untitled"),
        "version": info.get("version", "unknown"),
        "description": info.get("description"),
        "server_urls": [s.get("url") for s in servers if isinstance(s, dict) and "url" in s],
        "path_count": len(paths),
        "operation_count": operation_count,
    }
