"""Tool registry: approved tools exposed to the agent with argument validation.

The agent may only call tools registered here.
Model-generated arguments are validated against schemas before execution.
No arbitrary shell commands or filesystem access outside the workspace.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

log = logging.getLogger(__name__)

ToolHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, Any]]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters_schema: dict[str, Any]   # JSON Schema for the parameters object
    handler: Any                         # async callable


class ToolCallError(ValueError):
    """Raised when tool argument validation fails."""


class ToolRegistry:
    """Maintains the set of tools the agent is allowed to invoke."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters_schema: dict[str, Any],
        handler: ToolHandler,
    ) -> None:
        self._tools[name] = ToolSpec(
            name=name,
            description=description,
            parameters_schema=parameters_schema,
            handler=handler,
        )
        log.debug("Tool registered: %s", name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def to_openai_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters_schema,
                },
            }
            for t in self._tools.values()
        ]

    def to_anthropic_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters_schema,
            }
            for t in self._tools.values()
        ]

    async def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        """Validate *arguments* and call the registered handler.

        Raises ToolCallError if the tool is unknown or arguments are invalid.
        """
        spec = self._tools.get(name)
        if spec is None:
            raise ToolCallError(f"Unknown tool: '{name}'. Allowed: {self.names()}")

        # Validate arguments against the JSON schema using jsonschema if available,
        # otherwise do a basic required-key check.
        self._validate_args(spec, arguments)

        log.debug("Invoking tool '%s' with args: %s", name, list(arguments.keys()))
        try:
            result = await spec.handler(arguments)
            return result
        except ToolCallError:
            raise
        except Exception as exc:
            log.warning("Tool '%s' raised: %s", name, exc)
            return {"error": str(exc), "tool": name}

    @staticmethod
    def _validate_args(spec: ToolSpec, arguments: dict[str, Any]) -> None:
        schema = spec.parameters_schema
        required = schema.get("required", [])
        for key in required:
            if key not in arguments:
                raise ToolCallError(
                    f"Tool '{spec.name}': missing required argument '{key}'."
                )

        try:
            import jsonschema  # type: ignore[import]
            jsonschema.validate(instance=arguments, schema=schema)
        except ImportError:
            pass  # jsonschema not installed — basic required-key check above is sufficient.
        except Exception as exc:
            raise ToolCallError(
                f"Tool '{spec.name}': argument validation failed — {exc}"
            ) from exc
