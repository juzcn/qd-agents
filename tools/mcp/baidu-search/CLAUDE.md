# CLAUDE.md - baidu-search MCP Server

This MCP server provides access to the baidu-search skill via the Model Context Protocol.

## Architecture

- `baidu-search/main.py` - Main MCP server implementation (Python)
- `skill_wrapper.py` - Python wrapper for the original skill
- `pyproject.toml` - Python project configuration and dependencies
- `test/validate.py` - Python validation script to verify server functionality

## Development

This is a Python MCP server using uv for package management.

```bash
# Install uv (if not already installed)
pip install uv

# Install dependencies
uv pip install -e .

# Run the server
uv run baidu-search -m baidu-search.main

# Or run directly
python -m baidu-search.main
```

## Configuration

The server reads configuration from the original skill directory.

## Package Management

This project uses uv for fast, reliable Python package management.

## Notes

This is an auto-generated MCP server wrapper for the baidu-search skill.
