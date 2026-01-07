# MCP LLM Chat

Interactive terminal chat that connects OpenAI's Responses API to your MCP Hub.

## Prerequisites

- MCP Hub running on localhost:8000
- At least one spec enabled (httpbin, dog, dailymed, etc.)
- ngrok account (free tier works)
- OpenAI API key

## Installation

```bash
pip install openai pyngrok httpx
```

## Setup

1. Start the MCP Hub:

```bash
uvicorn app.main:app --reload --port 8000
```

2. Upload and enable a spec:

```bash
curl -X POST "http://localhost:8000/specs/upload" -F "name=httpbin" -F "file=@examples/httpbin.yaml"
curl -X POST "http://localhost:8000/specs/httpbin/enable"
```

3. Configure ngrok:

```bash
export NGROK_AUTHTOKEN="your-token"
```

4. Set OpenAI key:

```bash
export OPENAI_API_KEY="sk-..."
```

## Usage

```bash
python scripts/mcp_llm_chat.py
```

## Environment Variables

| Variable        | Required | Default       | Description                                                               |
| --------------- | -------- | ------------- | ------------------------------------------------------------------------- |
| OPENAI_API_KEY  | Yes      | -             | OpenAI API key (from https://platform.openai.com/api-keys)                |
| MODEL           | No       | gpt-5.2       | Model to use (gpt-5.2, gpt-4o, gpt-4o-mini)      |
| HUB_PORT        | No       | 8000          | Local hub port                                                            |
| MCP_MAP         | No       | auto-discover | Server mapping (auto-discovers enabled specs by default)                  |
| NGROK_AUTHTOKEN | No       | -             | ngrok token (from https://dashboard.ngrok.com/get-started/your-authtoken) |

**MCP_MAP**: If not set, the script automatically discovers all enabled specs from the hub. You can manually override with: `export MCP_MAP='{"myspec": "/mcp/myspec/mcp"}'`

## Commands

- `:servers` - List MCP servers and URLs
- `:tools` - Ask model to list tools
- `:reset` - Clear history
- `exit` - Quit

## Example

```
You: httpbin_get foo=hello bar=world

Assistant: Called httpbin_get. Response: {"args": {"foo": "hello", "bar": "world"}}

You: Get a random dog image

Assistant: Here is a random dog image: https://images.dog.ceo/breeds/...
```

## DailyMed Example

```
You: Search for aspirin drug labels

Assistant: I found 25 SPL documents for aspirin. Here are the top results:
1. ASPIRIN tablet (setid: abc123...)
2. ASPIRIN DELAYED RELEASE tablet (setid: def456...)

You: Get details for the first one

Assistant: Here are the details for ASPIRIN tablet:
- Labeler: Example Pharma
- Published: 2024-02-15
- Active Ingredient: Aspirin 325mg
```
