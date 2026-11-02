"""Tests for the agent tool registry."""

import pytest
from rea.agent.tool_registry import ToolCallError, ToolRegistry


@pytest.fixture
def registry():
    reg = ToolRegistry()

    async def echo_handler(args):
        return {"echo": args.get("message", "")}

    reg.register(
        name="echo",
        description="Echo a message.",
        parameters_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to echo."}
            },
            "required": ["message"],
        },
        handler=echo_handler,
    )
    return reg


@pytest.mark.asyncio
async def test_invoke_registered_tool(registry):
    result = await registry.invoke("echo", {"message": "hello"})
    assert result == {"echo": "hello"}


@pytest.mark.asyncio
async def test_unknown_tool_raises(registry):
    with pytest.raises(ToolCallError, match="Unknown tool"):
        await registry.invoke("nonexistent", {})


@pytest.mark.asyncio
async def test_missing_required_arg_raises(registry):
    with pytest.raises(ToolCallError, match="missing required argument"):
        await registry.invoke("echo", {})


def test_openai_schema_format(registry):
    schemas = registry.to_openai_schemas()
    assert len(schemas) == 1
    schema = schemas[0]
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "echo"
    assert "message" in schema["function"]["parameters"]["properties"]


def test_anthropic_schema_format(registry):
    schemas = registry.to_anthropic_schemas()
    assert len(schemas) == 1
    assert schemas[0]["name"] == "echo"
    assert "input_schema" in schemas[0]


def test_tool_names(registry):
    assert "echo" in registry.names()
