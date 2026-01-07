"""ASGI dispatcher for routing MCP requests to spec-specific MCP servers."""

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.registry import SpecRegistry


class LifespanManager:
    """Manages the lifespan contexts for dynamically mounted MCP apps."""

    def __init__(self) -> None:
        self._started: dict[str, asyncio.Task] = {}
        self._cleanup_events: dict[str, asyncio.Event] = {}
        self._initialized: dict[str, asyncio.Event] = {}

    async def ensure_started(self, name: str, app: Any) -> None:
        """Ensure the lifespan for an app is started."""
        if name in self._started:
            # Already started, wait for initialization
            await self._initialized[name].wait()
            return

        # Create events for this app
        self._cleanup_events[name] = asyncio.Event()
        self._initialized[name] = asyncio.Event()

        # Start lifespan in background task
        async def run_lifespan() -> None:
            try:
                # Get the lifespan function and call it
                # It returns an async context manager
                lifespan_fn = app.lifespan
                ctx_manager = lifespan_fn(app)

                # Use async with to properly manage the lifespan
                async with ctx_manager:
                    # Signal that we're initialized
                    self._initialized[name].set()

                    # Wait until we're told to shut down
                    await self._cleanup_events[name].wait()

                # Shutdown happens automatically when exiting the context
            except Exception as e:
                import sys
                print(f"Error in lifespan for {name}: {e}", file=sys.stderr)
                self._initialized[name].set()  # Unblock waiters

        self._started[name] = asyncio.create_task(run_lifespan())
        # Wait for initialization
        await self._initialized[name].wait()

    async def stop(self, name: str) -> None:
        """Stop the lifespan for an app."""
        if name not in self._started:
            return

        # Signal shutdown
        self._cleanup_events[name].set()
        # Wait for task to complete
        try:
            await asyncio.wait_for(self._started[name], timeout=5.0)
        except asyncio.TimeoutError:
            self._started[name].cancel()
        except Exception:
            pass
        # Clean up
        self._started.pop(name, None)
        self._cleanup_events.pop(name, None)
        self._initialized.pop(name, None)

    async def stop_all(self) -> None:
        """Stop all lifespans."""
        for name in list(self._started.keys()):
            await self.stop(name)


# Global lifespan manager
_lifespan_manager = LifespanManager()


async def stop_spec_lifespan(name: str) -> None:
    """Stop the lifespan for a spec (call when disabling)."""
    await _lifespan_manager.stop(name)


async def stop_all_lifespans() -> None:
    """Stop all MCP app lifespans (call on shutdown)."""
    await _lifespan_manager.stop_all()


class MCPDispatcher:
    """
    ASGI middleware that routes requests to spec-specific MCP servers.

    Mounted at /mcp, it extracts the spec name from the first path segment
    and forwards requests to the corresponding MCP HTTP app.

    Example:
        /mcp/dog/sse -> routes to dog's MCP app at /sse
        /mcp/httpbin/messages -> routes to httpbin's MCP app at /messages
    """

    def __init__(self, registry: "SpecRegistry") -> None:
        self.registry = registry

    async def __call__(self, scope: dict, receive: callable, send: callable) -> None:
        """ASGI entry point."""
        if scope["type"] not in ("http", "websocket"):
            # Pass through non-HTTP/WS requests
            return

        path = scope.get("path", "/")
        root_path = scope.get("root_path", "")

        # Strip the mount prefix from path if present
        # FastAPI mount sets root_path but may not strip path
        if root_path and path.startswith(root_path):
            path = path[len(root_path):]
        if not path.startswith("/"):
            path = "/" + path

        # Extract spec name from path (first segment)
        # Path will be like /{spec_name}/... after stripping mount prefix
        path_parts = path.strip("/").split("/", 1)

        if not path_parts or not path_parts[0]:
            # No spec name in path
            await self._send_error(send, 404, "Spec name required in path: /mcp/{spec_name}/...")
            return

        spec_name = path_parts[0]
        remaining_path = "/" + (path_parts[1] if len(path_parts) > 1 else "")

        # Look up the MCP app for this spec
        mcp_app = self.registry.get_mcp_app(spec_name)

        if mcp_app is None:
            # Spec doesn't exist or isn't enabled
            entry = self.registry.get(spec_name)
            if entry is None:
                await self._send_error(send, 404, f"Spec '{spec_name}' not found")
            else:
                await self._send_error(
                    send, 404, f"Spec '{spec_name}' is not enabled for MCP exposure"
                )
            return

        # Ensure the MCP app's lifespan is started
        await _lifespan_manager.ensure_started(spec_name, mcp_app)

        # Rewrite scope for the downstream MCP app
        # Update path to remove the spec_name prefix
        modified_scope = scope.copy()
        modified_scope["path"] = remaining_path

        # Update root_path to include the spec_name (for proper URL generation)
        original_root_path = scope.get("root_path", "")
        modified_scope["root_path"] = f"{original_root_path}/{spec_name}"

        # Forward to the spec's MCP app
        await mcp_app(modified_scope, receive, send)

    async def _send_error(self, send: callable, status_code: int, message: str) -> None:
        """Send an HTTP error response."""
        import json

        body = json.dumps({"detail": message}).encode("utf-8")

        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"content-length", str(len(body)).encode()],
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": body,
            }
        )
