"""FastAPI application for the MCP Hub control plane and data plane."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.mcp_dispatcher import MCPDispatcher, stop_all_lifespans, stop_spec_lifespan
from app.models import (
    ErrorResponse,
    SourceType,
    SpecDeleteResponse,
    SpecDetail,
    SpecDisableResponse,
    SpecEnableResponse,
    SpecListItem,
    SpecUploadResponse,
    ValidationStatus,
)
from app.openapi_loader import OpenAPILoadError, load_from_string
from app.openapi_to_mcp import OpenAPIConversionError, build_mcp_http_app
from app.registry import registry

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - cleanup MCP lifespans on shutdown."""
    yield
    # On shutdown, stop all MCP app lifespans
    await stop_all_lifespans()


# Create FastAPI app
app = FastAPI(
    title="MCP Hub",
    description="Dynamic OpenAPI to MCP server generator",
    version="0.1.0",
    lifespan=lifespan,
)


# --- Control Plane Endpoints ---


@app.post(
    "/specs/upload",
    response_model=SpecUploadResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid spec"},
        409: {"model": ErrorResponse, "description": "Spec already exists"},
    },
    tags=["Control Plane"],
    summary="Upload an OpenAPI spec",
)
async def upload_spec(
    name: str = Form(..., description="Unique name for this spec"),
    file: UploadFile = File(..., description="OpenAPI spec file (YAML or JSON)"),
    base_url_override: str | None = Form(
        None, description="Override for the upstream API base URL"
    ),
) -> SpecUploadResponse:
    """
    Upload and register an OpenAPI spec.

    The spec will be validated but not enabled for MCP exposure until
    explicitly enabled via POST /specs/{name}/enable.
    """
    # Check for duplicate name
    if registry.exists(name):
        raise HTTPException(status_code=409, detail=f"Spec '{name}' already exists")

    # Read and parse the file
    try:
        content = await file.read()
        parsed_spec, validation_errors = load_from_string(content)
    except OpenAPILoadError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Determine validation status
    validation_status = (
        ValidationStatus.INVALID if validation_errors else ValidationStatus.VALID
    )

    # Register the spec
    registry.register(
        name=name,
        raw_text=content.decode("utf-8") if isinstance(content, bytes) else content,
        parsed_spec=parsed_spec,
        source_type=SourceType.UPLOAD,
        base_url_override=base_url_override,
        validation_status=validation_status,
        validation_errors=validation_errors,
    )

    return SpecUploadResponse(
        name=name,
        validation_status=validation_status,
        validation_errors=validation_errors,
        message=(
            f"Spec '{name}' registered successfully"
            if not validation_errors
            else f"Spec '{name}' registered with validation errors"
        ),
    )


@app.get(
    "/specs",
    response_model=list[SpecListItem],
    tags=["Control Plane"],
    summary="List all registered specs",
)
async def list_specs() -> list[SpecListItem]:
    """List all registered OpenAPI specs with summary information."""
    return registry.list_all()


@app.get(
    "/specs/{name}",
    response_model=SpecDetail,
    responses={404: {"model": ErrorResponse}},
    tags=["Control Plane"],
    summary="Get spec details",
)
async def get_spec(name: str) -> SpecDetail:
    """Get detailed information about a specific spec."""
    try:
        metadata = registry.get_metadata(name)
        return SpecDetail(**metadata.model_dump())
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Spec '{name}' not found")


@app.post(
    "/specs/{name}/enable",
    response_model=SpecEnableResponse,
    responses={
        404: {"model": ErrorResponse},
        400: {"model": ErrorResponse, "description": "Cannot enable invalid spec"},
    },
    tags=["Control Plane"],
    summary="Enable MCP exposure for a spec",
)
async def enable_spec(name: str) -> SpecEnableResponse:
    """
    Enable MCP exposure for a spec.

    This generates the FastMCP server with tools for each operation
    and makes it available at /mcp/{name}/.
    """
    entry = registry.get(name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Spec '{name}' not found")

    if entry.validation_status == ValidationStatus.INVALID:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot enable spec '{name}': validation errors: {entry.validation_errors}",
        )

    # Build the MCP HTTP app
    try:
        mcp_app, tool_names = build_mcp_http_app(
            spec_name=name,
            spec=entry.parsed_spec,
            base_url_override=entry.base_url_override,
        )
    except OpenAPIConversionError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Enable in registry
    registry.enable(name, mcp_app, tool_names)

    return SpecEnableResponse(
        name=name,
        enabled=True,
        tool_count=len(tool_names),
        tool_names=tool_names,
        message=f"Spec '{name}' enabled with {len(tool_names)} tools",
    )


@app.post(
    "/specs/{name}/disable",
    response_model=SpecDisableResponse,
    responses={404: {"model": ErrorResponse}},
    tags=["Control Plane"],
    summary="Disable MCP exposure for a spec",
)
async def disable_spec(name: str) -> SpecDisableResponse:
    """
    Disable MCP exposure for a spec.

    The spec remains registered but is no longer available via /mcp/{name}/.
    """
    try:
        # Stop the MCP app's lifespan if running
        await stop_spec_lifespan(name)
        registry.disable(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Spec '{name}' not found")

    return SpecDisableResponse(
        name=name,
        enabled=False,
        message=f"Spec '{name}' disabled",
    )


@app.delete(
    "/specs/{name}",
    response_model=SpecDeleteResponse,
    responses={404: {"model": ErrorResponse}},
    tags=["Control Plane"],
    summary="Delete a spec",
)
async def delete_spec(name: str) -> SpecDeleteResponse:
    """
    Delete a spec from the registry.

    This also disables MCP exposure if the spec was enabled.
    """
    try:
        # Stop the MCP app's lifespan if running
        await stop_spec_lifespan(name)
        registry.delete(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Spec '{name}' not found")

    return SpecDeleteResponse(
        name=name,
        message=f"Spec '{name}' deleted",
    )


# --- Health Check ---


@app.get("/health", tags=["System"], summary="Health check")
async def health_check() -> dict:
    """Check if the service is healthy."""
    return {"status": "healthy", "specs_count": len(registry.list_all())}


# --- Mount MCP Dispatcher ---

# Mount the MCP dispatcher at /mcp to handle all MCP requests
app.mount("/mcp", MCPDispatcher(registry))
