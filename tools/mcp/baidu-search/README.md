# baidu-search MCP Server

Search the web using Baidu AI Search Engine (BDSE). Use for live information, documentation, or research topics.

## Overview

This MCP server wraps the baidu-search skill to make it available via the Model Context Protocol.

## Tools

The server provides a single tool:

### `baidu-search`
- **Description**: Search the web using Baidu AI Search Engine (BDSE). Use for live information, documentation, or research topics.
- **Parameters**:
  - `query` (str): Search query
  - `edition` (str, optional): Search edition: `standard` (full) or `lite` (light) (default: standard)
  - `resource_type_filter` (list[obj], optional): Resource types: web (max 50), video (max 10), image (max 30), aladdin (max 5) (default: [{"type": "web", "top_k": 20}])
  - `search_filter` (obj, optional): Advanced filters including site matching and date range filtering (default: {})
  - `block_websites` (list[str], optional): Sites to block, e.g. ["tieba.baidu.com"] (default: null)
  - `search_recency_filter` (str, optional): Time filter: `week`, `month`, `semiyear`, `year` (default: year)
  - `safe_search` (bool, optional): Enable strict content filtering (default: false)

## Usage

```bash
# Start the MCP server
uv run baidu-search -m scripts.main
```

## Configuration

Copy the skill configuration from the original skill directory.

## Development

This is a Python MCP server using uv for package management.

```bash
# Install uv (if not already installed)
pip install uv

# Install dependencies
uv pip install -e .

# Run the server directly
python -m scripts.main
```

## Package Management

This project uses uv for fast, reliable Python package management.

## License

MIT
