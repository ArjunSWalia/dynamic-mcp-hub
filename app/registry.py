"""In-memory registry for OpenAPI specs and their generated MCP servers."""

from typing import Any, Callable

from app.models import (
    SourceType,
    SpecEntry,
    SpecListItem,
    SpecMetadata,
    ValidationStatus,
)


class SpecRegistry:
    """
    Thread-safe in-memory registry for OpenAPI specs.

    Stores spec metadata, parsed content, and generated MCP ASGI apps.
    """

    def __init__(self) -> None:
        self._specs: dict[str, SpecEntry] = {}
        self._mcp_apps: dict[str, Callable] = {}  # spec_name -> ASGI app

    def exists(self, name: str) -> bool:
        """Check if a spec with the given name exists."""
        return name in self._specs

    def register(
        self,
        name: str,
        raw_text: str,
        parsed_spec: dict[str, Any],
        source_type: SourceType,
        base_url_override: str | None = None,
        validation_status: ValidationStatus = ValidationStatus.VALID,
        validation_errors: list[str] | None = None,
    ) -> SpecEntry:
        """
        Register a new spec in the registry.

        Raises ValueError if spec with name already exists.
        """
        if self.exists(name):
            raise ValueError(f"Spec '{name}' already exists")

        entry = SpecEntry(
            name=name,
            source_type=source_type,
            raw_text=raw_text,
            parsed_spec=parsed_spec,
            enabled=False,
            validation_status=validation_status,
            validation_errors=validation_errors or [],
            base_url_override=base_url_override,
            tool_names=[],
        )
        self._specs[name] = entry
        return entry

    def get(self, name: str) -> SpecEntry | None:
        """Get a spec entry by name, or None if not found."""
        return self._specs.get(name)

    def get_or_raise(self, name: str) -> SpecEntry:
        """Get a spec entry by name, raising KeyError if not found."""
        entry = self.get(name)
        if entry is None:
            raise KeyError(f"Spec '{name}' not found")
        return entry

    def list_all(self) -> list[SpecListItem]:
        """List all registered specs with summary info."""
        return [
            SpecListItem(
                name=entry.name,
                enabled=entry.enabled,
                source_type=entry.source_type,
                validation_status=entry.validation_status,
                validation_errors=entry.validation_errors,
                base_url_override=entry.base_url_override,
            )
            for entry in self._specs.values()
        ]

    def get_metadata(self, name: str) -> SpecMetadata:
        """Get metadata for a spec."""
        entry = self.get_or_raise(name)
        return SpecMetadata(
            name=entry.name,
            enabled=entry.enabled,
            source_type=entry.source_type,
            validation_status=entry.validation_status,
            validation_errors=entry.validation_errors,
            base_url_override=entry.base_url_override,
            tool_names=entry.tool_names,
        )

    def enable(self, name: str, mcp_http_app: Callable, tool_names: list[str]) -> None:
        """
        Enable MCP exposure for a spec.

        Args:
            name: Spec name
            mcp_http_app: The ASGI app from FastMCP.http_app()
            tool_names: List of generated tool names
        """
        entry = self.get_or_raise(name)
        entry.enabled = True
        entry.tool_names = tool_names
        self._mcp_apps[name] = mcp_http_app

    def disable(self, name: str) -> None:
        """Disable MCP exposure for a spec."""
        entry = self.get_or_raise(name)
        entry.enabled = False
        entry.tool_names = []
        self._mcp_apps.pop(name, None)

    def delete(self, name: str) -> None:
        """Remove a spec from the registry entirely."""
        if not self.exists(name):
            raise KeyError(f"Spec '{name}' not found")
        self.disable(name)  # Clean up MCP app if enabled
        del self._specs[name]

    def get_mcp_app(self, name: str) -> Callable | None:
        """Get the MCP ASGI app for a spec, or None if not enabled."""
        entry = self.get(name)
        if entry is None or not entry.enabled:
            return None
        return self._mcp_apps.get(name)

    def is_enabled(self, name: str) -> bool:
        """Check if a spec is enabled for MCP exposure."""
        entry = self.get(name)
        return entry is not None and entry.enabled


# Global registry instance
registry = SpecRegistry()
