"""Pydantic models for the MCP Hub control plane."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SourceType(str, Enum):
    """How the spec was provided to the hub."""

    UPLOAD = "upload"
    URL = "url"
    PATH = "path"


class ValidationStatus(str, Enum):
    """Validation state of an OpenAPI spec."""

    VALID = "valid"
    INVALID = "invalid"
    PENDING = "pending"


class SpecMetadata(BaseModel):
    """Metadata about a registered OpenAPI spec."""

    name: str = Field(..., description="Unique identifier for this spec")
    enabled: bool = Field(default=False, description="Whether MCP exposure is active")
    source_type: SourceType = Field(..., description="How the spec was loaded")
    validation_status: ValidationStatus = Field(
        default=ValidationStatus.PENDING, description="Spec validation state"
    )
    validation_errors: list[str] = Field(
        default_factory=list, description="Validation error messages if invalid"
    )
    base_url_override: str | None = Field(
        default=None, description="Override for the upstream API base URL"
    )
    tool_names: list[str] = Field(
        default_factory=list, description="Generated MCP tool names (when enabled)"
    )


class SpecListItem(BaseModel):
    """Summary info for listing specs."""

    name: str
    enabled: bool
    source_type: SourceType
    validation_status: ValidationStatus
    validation_errors: list[str] = []
    base_url_override: str | None = None


class SpecDetail(SpecMetadata):
    """Detailed spec info including derived tool names."""

    pass


class SpecUploadResponse(BaseModel):
    """Response after uploading a spec."""

    name: str
    validation_status: ValidationStatus
    validation_errors: list[str] = []
    message: str


class SpecEnableResponse(BaseModel):
    """Response after enabling a spec."""

    name: str
    enabled: bool
    tool_count: int
    tool_names: list[str]
    message: str


class SpecDisableResponse(BaseModel):
    """Response after disabling a spec."""

    name: str
    enabled: bool
    message: str


class SpecDeleteResponse(BaseModel):
    """Response after deleting a spec."""

    name: str
    message: str


class ErrorResponse(BaseModel):
    """Standard error response."""

    detail: str


class SpecEntry(BaseModel):
    """Internal registry entry for a spec (not exposed via API directly)."""

    name: str
    source_type: SourceType
    raw_text: str = Field(..., description="Original spec content as string")
    parsed_spec: dict[str, Any] = Field(..., description="Parsed OpenAPI spec dict")
    enabled: bool = False
    validation_status: ValidationStatus = ValidationStatus.PENDING
    validation_errors: list[str] = Field(default_factory=list)
    base_url_override: str | None = None
    tool_names: list[str] = Field(default_factory=list)
    # mcp_http_app stored separately in registry (not serializable)

    class Config:
        arbitrary_types_allowed = True
