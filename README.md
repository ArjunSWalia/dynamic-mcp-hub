# MCP Hub

Dynamic OpenAPI to MCP server generator. Upload OpenAPI specs and instantly expose them as MCP (Model Context Protocol) servers.

## Features

- **Dynamic MCP Generation**: Convert any OpenAPI 3.x spec to an MCP server on-the-fly
- **Control Plane API**: REST API to manage specs (upload, enable, disable, delete)
- **Multi-spec Support**: Run multiple MCP servers simultaneously under `/mcp/{spec_name}/`
- **Zero Configuration**: Tools are automatically generated from OpenAPI operations

## Quick Start

### 1. Install Dependencies

```bash
# Using pip
pip install -e .

# Or install dependencies directly
pip install fastapi uvicorn httpx pyyaml python-multipart pydantic fastmcp
```

### 2. Run the Server

```bash
uvicorn app.main:app --reload --port 8000
```

The server starts at `http://localhost:8000`.

### 3. Register an OpenAPI Spec

```bash
# Upload the HTTPBin example spec
curl -X POST "http://localhost:8000/specs/upload" \
  -F "name=httpbin" \
  -F "file=@examples/httpbin.yaml"
```

### 4. Enable MCP Exposure

```bash
curl -X POST "http://localhost:8000/specs/httpbin/enable"
```

### 5. Connect Your MCP Client

The MCP server is now available at `http://localhost:8000/mcp/httpbin/`

For MCP clients (e.g., Claude Desktop with HTTP transport):

```
http://localhost:8000/mcp/httpbin/mcp
```

## API Reference

### Control Plane Endpoints

| Method | Endpoint                | Description                             |
| ------ | ----------------------- | --------------------------------------- |
| POST   | `/specs/upload`         | Upload an OpenAPI spec (multipart form) |
| GET    | `/specs`                | List all registered specs               |
| GET    | `/specs/{name}`         | Get spec details and tool names         |
| POST   | `/specs/{name}/enable`  | Enable MCP exposure                     |
| POST   | `/specs/{name}/disable` | Disable MCP exposure                    |
| DELETE | `/specs/{name}`         | Delete a spec                           |
| GET    | `/health`               | Health check                            |

### Upload Parameters

- `name` (required): Unique identifier for the spec
- `file` (required): OpenAPI spec file (YAML or JSON)
- `base_url_override` (optional): Override the upstream API base URL

### MCP Data Plane

Once a spec is enabled, its MCP server is available at:

```
/mcp/{spec_name}/
```

## Tool Generation

Tools are generated from OpenAPI operations with these rules:

### Tool Naming

- Uses `operationId` if present in the spec
- Otherwise: `METHOD__sanitized_path` (e.g., `GET__pets_petId`)

### Tool Inputs

- Path parameters → required fields
- Query parameters → optional fields (unless `required: true`)
- JSON request body → `body` field (dict type)

### Tool Outputs

- JSON responses are returned as-is
- Non-JSON responses return `{"text": "...", "status_code": ..., "content_type": "..."}`

## Examples

### Register and Enable Dog API

```bash
# Upload
curl -X POST "http://localhost:8000/specs/upload" \
  -F "name=dog" \
  -F "file=@examples/dog_api.yaml"

# Enable
curl -X POST "http://localhost:8000/specs/dog/enable"

# List tools
curl "http://localhost:8000/specs/dog"
```

### With Base URL Override

```bash
curl -X POST "http://localhost:8000/specs/upload" \
  -F "name=myapi" \
  -F "file=@myspec.yaml" \
  -F "base_url_override=https://api.example.com/v1"
```

### Claude Desktop Configuration

Add to your Claude Desktop MCP config:

```json
{
  "mcpServers": {
    "httpbin": {
      "url": "http://localhost:8000/mcp/httpbin/mcp"
    },
    "dog": {
      "url": "http://localhost:8000/mcp/dog/mcp"
    }
  }
}
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        MCP Hub                               │
├─────────────────────────────────────────────────────────────┤
│  Control Plane (/specs/*)     │    Data Plane (/mcp/*)      │
│  ┌─────────────────────────┐  │  ┌─────────────────────────┐│
│  │ POST /specs/upload      │  │  │   MCPDispatcher         ││
│  │ GET  /specs             │  │  │   ┌─────────────────┐   ││
│  │ GET  /specs/{name}      │  │  │   │ /mcp/httpbin/   │───┼┼──► HTTPBin API
│  │ POST /specs/{name}/enable│ │  │   │ /mcp/dog/      │───┼┼──► Dog API
│  │ POST /specs/{name}/disable│ │ │   │ /mcp/{spec}/    │───┼┼──► Any API
│  │ DELETE /specs/{name}    │  │  │   └─────────────────┘   ││
│  └─────────────────────────┘  │  └─────────────────────────┘│
│              │                │              │               │
│              ▼                │              ▼               │
│  ┌─────────────────────────────────────────────────────────┐│
│  │                    SpecRegistry                          ││
│  │   - Stores spec metadata & parsed content               ││
│  │   - Manages enable/disable state                        ││
│  │   - Holds generated FastMCP http_app instances          ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

## Limitations (MVP)

- **In-memory storage**: Specs are lost on restart
- **No authentication**: Auth headers are not forwarded to upstream APIs
- **JSON bodies only**: Only `application/json` request bodies are supported
- **OpenAPI 3.x only**: Swagger 2.0 specs are not supported

## Project Structure

```
mcp_hub/
├── app/
│   ├── __init__.py
│   ├── main.py           # FastAPI app, routes, MCP mount
│   ├── models.py         # Pydantic models
│   ├── registry.py       # In-memory spec registry
│   ├── openapi_loader.py # YAML/JSON parsing & validation
│   ├── openapi_to_mcp.py # OpenAPI → FastMCP conversion
│   └── mcp_dispatcher.py # ASGI dispatcher for /mcp/*
├── examples/
│   ├── httpbin.yaml      # HTTPBin OpenAPI spec
│   ├── dog_api.yaml      # Dog API OpenAPI spec
│   └── demo.md           # Demo walkthrough
├── pyproject.toml
└── README.md
```
