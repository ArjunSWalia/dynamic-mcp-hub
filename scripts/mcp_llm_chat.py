#!/usr/bin/env python3
"""
MCP LLM Chat - Interactive chat with OpenAI using MCP Hub tools.

This script:
1. Starts an ngrok tunnel to your local MCP Hub (localhost:8000)
2. Connects to OpenAI's Responses API with MCP tool support
3. Provides an interactive chat where the model can call MCP tools

Environment Variables:
    OPENAI_API_KEY  - Required. 
    MODEL           - Optional. Model to use (default: gpt-5.2)
    HUB_PORT        - Optional. Local hub port (default: 8000)

Setup:
    1. Set your OpenAI API key:
       export OPENAI_API_KEY="sk-proj-..."
    
    2. Set your ngrok auth token:
       export NGROK_AUTHTOKEN="your-token"
    
Usage:
    python scripts/mcp_llm_chat.py

Special Commands:
    :servers  - List MCP servers and their URLs
    :tools    - Ask the model to list available tools
    :reset    - Clear conversation history
    exit      - Quit the chat
"""

import json
import os
import sys
import time
from typing import Optional

# Check for required dependencies early
def check_dependencies():
    """Check that required packages are installed."""
    missing = []

    try:
        import openai
    except ImportError:
        missing.append("openai")

    try:
        import pyngrok
    except ImportError:
        missing.append("pyngrok")

    try:
        import httpx
    except ImportError:
        missing.append("httpx")

    if missing:
        print(f"Error: Missing required packages: {', '.join(missing)}")
        print(f"Install with: pip install {' '.join(missing)}")
        sys.exit(1)

check_dependencies()

import httpx
from openai import OpenAI
from pyngrok import ngrok, conf

# Configuration
DEFAULT_MODEL = "gpt-5.2"
DEFAULT_PORT = 8000

# Colors for terminal output
class Colors:
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    END = "\033[0m"


def colorize(text: str, color: str) -> str:
    """Add color to terminal output."""
    return f"{color}{text}{Colors.END}"


def print_error(msg: str):
    """Print an error message."""
    print(colorize(f"Error: {msg}", Colors.RED))


def print_info(msg: str):
    """Print an info message."""
    print(colorize(f"[INFO] {msg}", Colors.CYAN))


def print_success(msg: str):
    """Print a success message."""
    print(colorize(f"[OK] {msg}", Colors.GREEN))


def get_env_config() -> dict:
    """Load configuration from environment variables."""
    config = {
        "api_key": os.environ.get("OPENAI_API_KEY"),
        "model": os.environ.get("MODEL", DEFAULT_MODEL),
        "hub_port": int(os.environ.get("HUB_PORT", DEFAULT_PORT)),
        "ngrok_token": os.environ.get("NGROK_AUTHTOKEN"),
    }

    # Parse MCP_MAP if provided (manual override)
    mcp_map_str = os.environ.get("MCP_MAP")
    if mcp_map_str:
        try:
            config["mcp_map"] = json.loads(mcp_map_str)
        except json.JSONDecodeError:
            print_error("MCP_MAP environment variable is not valid JSON")
            sys.exit(1)
    else:
        # Will be auto-discovered later
        config["mcp_map"] = None

    return config


def check_hub_running(port: int) -> bool:
    """Check if the MCP Hub is running on the specified port."""
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(f"http://localhost:{port}/health")
            return response.status_code == 200
    except httpx.ConnectError:
        return False
    except Exception:
        return False


def discover_mcp_specs(port: int) -> dict[str, str]:
    """
    Auto-discover enabled MCP specs from the hub.
    
    Returns a dict mapping spec names to their MCP paths.
    Example: {"httpbin": "/mcp/httpbin/mcp", "dog": "/mcp/dog/mcp"}
    """
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(f"http://localhost:{port}/specs")
            if response.status_code != 200:
                return {}
            
            specs = response.json()
            # Filter for enabled specs only and build the map
            mcp_map = {}
            for spec in specs:
                if spec.get("enabled", False):
                    name = spec.get("name")
                    if name:
                        mcp_map[name] = f"/mcp/{name}/mcp"
            
            return mcp_map
    except Exception as e:
        print_error(f"Failed to discover specs: {e}")
        return {}


def check_mcp_endpoint(base_url: str, path: str) -> tuple[bool, Optional[str]]:
    """Check if an MCP endpoint is reachable."""
    full_url = f"{base_url}{path}"
    try:
        with httpx.Client(timeout=10.0) as client:
            # MCP endpoints respond to POST, but we can check connectivity
            # by attempting a GET (will return method not allowed, but connection works)
            response = client.get(full_url)
            # 405 Method Not Allowed is expected for MCP endpoints
            # 404 means the spec isn't enabled
            if response.status_code == 404:
                return False, f"MCP endpoint not found at {full_url} - is the spec enabled?"
            return True, None
    except httpx.ConnectError:
        return False, f"Cannot connect to {full_url}"
    except Exception as e:
        return False, f"Error checking {full_url}: {e}"


def start_ngrok_tunnel(port: int, auth_token: Optional[str] = None) -> str:
    """Start an ngrok tunnel and return the public URL."""
    print_info(f"Starting ngrok tunnel to localhost:{port}...")

    try:
        # Configure ngrok auth token if provided
        if auth_token:
            conf.get_default().auth_token = auth_token

        # Start the tunnel
        tunnel = ngrok.connect(port, "http")
        public_url = tunnel.public_url

        # Ensure HTTPS
        if public_url.startswith("http://"):
            public_url = public_url.replace("http://", "https://")

        print_success(f"ngrok tunnel established: {public_url}")
        return public_url

    except Exception as e:
        error_msg = str(e)
        if "authtoken" in error_msg.lower() or "authentication" in error_msg.lower():
            print_error("ngrok authentication failed.")
            print("  - Sign up at https://ngrok.com and get your auth token")
            print("  - Set NGROK_AUTHTOKEN environment variable, or")
            print("  - Run: ngrok config add-authtoken <YOUR_TOKEN>")
        elif "not found" in error_msg.lower() or "no such file" in error_msg.lower():
            print_error("ngrok is not installed or not in PATH.")
            print("  - Install ngrok: https://ngrok.com/download")
            print("  - Or install pyngrok: pip install pyngrok")
        else:
            print_error(f"Failed to start ngrok tunnel: {e}")
        sys.exit(1)


def build_mcp_tools(base_url: str, mcp_map: dict) -> list:
    """Build MCP tool definitions for OpenAI Responses API."""
    tools = []
    for label, path in mcp_map.items():
        server_url = f"{base_url}{path}"
        tools.append({
            "type": "mcp",
            "server_label": label,
            "server_url": server_url,
            "require_approval": "never"
        })
    return tools


def print_servers(base_url: str, mcp_map: dict):
    """Print the configured MCP servers."""
    print(colorize("\nConfigured MCP Servers:", Colors.BOLD))
    print("-" * 60)
    for label, path in mcp_map.items():
        full_url = f"{base_url}{path}"
        print(f"  {colorize(label, Colors.GREEN)}: {full_url}")
    print("-" * 60)
    print()


def print_welcome(base_url: str, mcp_map: dict, model: str):
    """Print welcome message."""
    print()
    print(colorize("=" * 60, Colors.CYAN))
    print(colorize("  MCP LLM Chat - Interactive AI with MCP Tools", Colors.BOLD))
    print(colorize("=" * 60, Colors.CYAN))
    print()
    print(f"  Model: {colorize(model, Colors.GREEN)}")
    print(f"  Hub URL: {colorize(base_url, Colors.GREEN)}")
    print(f"  MCP Servers: {colorize(str(len(mcp_map)), Colors.GREEN)}")
    print()
    print(colorize("Commands:", Colors.YELLOW))
    print("  :servers  - List MCP servers and URLs")
    print("  :tools    - Ask model to list available tools")
    print("  :reset    - Clear conversation history")
    print("  exit      - Quit")
    print()
    print(colorize("=" * 60, Colors.CYAN))
    print()


class MCPChat:
    """Interactive chat session with MCP tool support."""

    def __init__(self, client: OpenAI, model: str, tools: list, base_url: str, mcp_map: dict):
        self.client = client
        self.model = model
        self.tools = tools
        self.base_url = base_url
        self.mcp_map = mcp_map
        self.conversation_history = []
        self.system_prompt = """You are a helpful assistant with access to MCP (Model Context Protocol) tools.
You can call tools from the connected MCP servers to help answer questions.

When a user asks you to perform an action that requires calling an API:
1. Identify which MCP tool to use
2. Call the tool with appropriate parameters
3. Interpret the results and provide a helpful response

Be concise but informative in your responses."""

    def reset(self):
        """Clear conversation history."""
        self.conversation_history = []
        print_success("Conversation history cleared.")

    def chat(self, user_message: str) -> str:
        """Send a message and get a response."""
        # Add user message to history
        self.conversation_history.append({
            "role": "user",
            "content": user_message
        })

        try:
            # Build messages with system prompt
            messages = [{"role": "system", "content": self.system_prompt}]
            messages.extend(self.conversation_history)

            # Call OpenAI Responses API
            response = self.client.responses.create(
                model=self.model,
                input=messages,
                tools=self.tools
            )

            # Extract assistant response
            assistant_message = self._extract_response(response)

            # Add assistant response to history
            self.conversation_history.append({
                "role": "assistant",
                "content": assistant_message
            })

            return assistant_message

        except Exception as e:
            error_msg = str(e)

            # Check for specific error types
            if "api_key" in error_msg.lower() or "authentication" in error_msg.lower():
                return colorize("Error: Invalid OpenAI API key. Please check OPENAI_API_KEY.", Colors.RED)
            elif "rate_limit" in error_msg.lower():
                return colorize("Error: Rate limit exceeded. Please wait and try again.", Colors.RED)
            elif "mcp" in error_msg.lower() and ("unreachable" in error_msg.lower() or "connection" in error_msg.lower()):
                return colorize(f"Error: MCP server unreachable. Is the hub running? Error: {e}", Colors.RED)
            else:
                return colorize(f"Error: {e}", Colors.RED)

    def _extract_response(self, response) -> str:
        """Extract text content from OpenAI response."""
        # The Responses API returns output with different item types
        text_parts = []

        if hasattr(response, 'output'):
            for item in response.output:
                if hasattr(item, 'type'):
                    if item.type == 'message':
                        # Extract content from message
                        if hasattr(item, 'content'):
                            for content_item in item.content:
                                if hasattr(content_item, 'text'):
                                    text_parts.append(content_item.text)
                    elif item.type == 'text':
                        if hasattr(item, 'text'):
                            text_parts.append(item.text)

        if text_parts:
            return "\n".join(text_parts)

        # Fallback: try to get any text attribute
        if hasattr(response, 'output_text'):
            return response.output_text

        return "[No text response received]"

    def list_tools(self) -> str:
        """Ask the model to list its available tools."""
        return self.chat("Please list all the MCP tools you have access to, including their names, descriptions, and parameters.")


def main():
    """Main entry point."""
    # Load configuration
    config = get_env_config()

    # Validate API key
    if not config["api_key"]:
        print_error("OPENAI_API_KEY environment variable is not set.")
        print("  Set it with: export OPENAI_API_KEY='your-key-here'")
        sys.exit(1)

    # Check if hub is running
    print_info(f"Checking MCP Hub at localhost:{config['hub_port']}...")
    if not check_hub_running(config["hub_port"]):
        print_error(f"MCP Hub is not running on localhost:{config['hub_port']}")
        print(f"  Start it with: uvicorn app.main:app --reload --port {config['hub_port']}")
        sys.exit(1)
    print_success("MCP Hub is running")

    # Auto-discover MCP specs if not manually configured
    if config["mcp_map"] is None:
        print_info("Auto-discovering enabled MCP specs...")
        config["mcp_map"] = discover_mcp_specs(config["hub_port"])
        
        if not config["mcp_map"]:
            print_error("No enabled specs found on the hub.")
            print("  Enable a spec with: curl -X POST http://localhost:8000/specs/{name}/enable")
            sys.exit(1)
        
        print_success(f"Discovered {len(config['mcp_map'])} enabled spec(s): {', '.join(config['mcp_map'].keys())}")

    # Start ngrok tunnel
    public_url = start_ngrok_tunnel(config["hub_port"], config["ngrok_token"])

    # Verify MCP endpoints are accessible
    print_info("Verifying MCP endpoints...")
    available_servers = {}
    for label, path in config["mcp_map"].items():
        reachable, error = check_mcp_endpoint(public_url, path)
        if reachable:
            print_success(f"  {label}: OK")
            available_servers[label] = path
        else:
            print(colorize(f"  {label}: {error}", Colors.YELLOW))

    if not available_servers:
        print_error("No MCP endpoints are available. Enable at least one spec in the hub.")
        print("  Example: curl -X POST http://localhost:8000/specs/httpbin/enable")
        sys.exit(1)

    # Build MCP tools
    tools = build_mcp_tools(public_url, available_servers)

    # Initialize OpenAI client
    client = OpenAI(api_key=config["api_key"])

    # Create chat session
    chat = MCPChat(
        client=client,
        model=config["model"],
        tools=tools,
        base_url=public_url,
        mcp_map=available_servers
    )

    # Print welcome message
    print_welcome(public_url, available_servers, config["model"])

    # Interactive loop
    try:
        while True:
            try:
                # Get user input
                user_input = input(colorize("You: ", Colors.BLUE)).strip()

                if not user_input:
                    continue

                # Handle special commands
                if user_input.lower() == "exit":
                    print_info("Goodbye!")
                    break

                if user_input == ":servers":
                    print_servers(public_url, available_servers)
                    continue

                if user_input == ":tools":
                    print_info("Asking model to list available tools...")
                    response = chat.list_tools()
                    print(f"\n{colorize('Assistant:', Colors.GREEN)} {response}\n")
                    continue

                if user_input == ":reset":
                    chat.reset()
                    continue

                if user_input.startswith(":"):
                    print(colorize(f"Unknown command: {user_input}", Colors.YELLOW))
                    print("  Available: :servers, :tools, :reset, exit")
                    continue

                # Send to model
                response = chat.chat(user_input)
                print(f"\n{colorize('Assistant:', Colors.GREEN)} {response}\n")

            except EOFError:
                # Ctrl+D
                print()
                print_info("Goodbye!")
                break

    except KeyboardInterrupt:
        # Ctrl+C
        print()
        print_info("Interrupted. Goodbye!")

    finally:
        # Cleanup ngrok
        print_info("Shutting down ngrok tunnel...")
        ngrok.kill()


if __name__ == "__main__":
    main()
