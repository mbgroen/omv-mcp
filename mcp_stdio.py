"""
A dependency-free MCP server over stdio.

The official MCP Python SDK pulls in pydantic, which ships compiled binaries.
That makes it impossible to bundle a Python MCP server into a single .mcpb
extension that works on both macOS and Windows, and it raises the floor to
Python 3.10+. This module implements the small slice of the protocol a
tools-only server actually needs, using nothing but the standard library, so
omv-mcp runs on any Python 3.9+ with no installation step at all.

What is implemented
-------------------
* JSON-RPC 2.0 framed as newline-delimited JSON on stdin/stdout, per the MCP
  stdio transport.
* `initialize` with protocol version negotiation, `notifications/initialized`,
  and `ping`.
* `tools/list`, with an input schema derived from each function's signature,
  type hints and Google-style docstring.
* `tools/call`, returning text content, and reporting failures as
  `isError: true` rather than as a JSON-RPC error -- the spec reserves those
  for protocol-level problems.

Anything not implemented (resources, prompts, sampling, pagination) is simply
not advertised in the server capabilities, which is what the spec expects.

Note that stdout carries the protocol: nothing may be printed there. Use
stderr for diagnostics -- the `log()` helper does.
"""

from __future__ import annotations

import inspect
import json
import re
import sys
from typing import Any, Callable, Iterable

# Protocol revisions this server knows how to speak. If a client asks for one
# of these we echo it back; otherwise we answer with LATEST and let the client
# decide whether it can live with that, which is what the lifecycle spec
# prescribes. Add newer revisions here once they are verified.
KNOWN_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
LATEST_PROTOCOL_VERSION = "2025-06-18"

# JSON-RPC error codes used here.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Maps a type annotation to a JSON Schema fragment. Annotations arrive as
# strings because every module here uses `from __future__ import annotations`,
# and evaluating them is not an option on Python 3.9 where `dict | None` is a
# syntax the runtime does not understand. String matching keeps this working
# across versions without an eval.
_JSON_TYPES = {
    "str": {"type": "string"},
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "bool": {"type": "boolean"},
    "dict": {"type": "object"},
    "list": {"type": "array"},
}

_OPTIONAL_RE = re.compile(r"^Optional\[(.+)\]$")
_UNION_NONE_RE = re.compile(r"^(.+?)\s*\|\s*None$|^None\s*\|\s*(.+)$")


def log(message: str) -> None:
    """Write a diagnostic line to stderr. Never use print() -- stdout is the wire."""
    print(message, file=sys.stderr, flush=True)


def _strip_optional(annotation: str) -> tuple[str, bool]:
    """Reduce `Optional[int]` / `int | None` to `int`, reporting nullability."""
    annotation = annotation.strip()

    optional_match = _OPTIONAL_RE.match(annotation)
    if optional_match:
        return optional_match.group(1).strip(), True

    union_match = _UNION_NONE_RE.match(annotation)
    if union_match:
        inner = union_match.group(1) or union_match.group(2) or ""
        return inner.strip(), True

    return annotation, False


def _schema_for(annotation: Any) -> dict[str, Any]:
    """
    Build a JSON Schema fragment for one parameter annotation.

    Unrecognised annotations deliberately produce an empty schema, which in
    JSON Schema means "anything goes". A wrong constraint would make a client
    refuse a legitimate call; no constraint merely means less validation.
    """
    if annotation is inspect.Parameter.empty:
        return {}

    text = annotation if isinstance(annotation, str) else getattr(
        annotation, "__name__", str(annotation)
    )

    # A forward reference written as a string literal, such as
    # `def f(x: "dict | None")`, is stringified by PEP 563 with its quotes
    # intact, arriving here as the six characters 'dict'. Peel those off.
    text = text.strip()
    while len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()

    base, _ = _strip_optional(text)

    # Subscripted generics such as list[str] or dict[str, Any] keep their
    # container type; the element type is not expressed here.
    container = base.split("[", 1)[0].strip()
    return dict(_JSON_TYPES.get(container, {}))


def _parse_docstring(doc: str | None) -> tuple[str, dict[str, str]]:
    """
    Split a Google-style docstring into a summary and per-argument descriptions.

    Everything before the `Args:` block becomes the tool description; the
    entries inside it become per-property descriptions. Sections that follow,
    such as `Examples:`, are folded back into the description because they help
    a model call the tool correctly.
    """
    if not doc:
        return "", {}

    lines = inspect.cleandoc(doc).splitlines()
    description_parts: list[str] = []
    args: dict[str, str] = {}

    in_args = False
    current: str | None = None

    for line in lines:
        stripped = line.strip()

        if stripped in ("Args:", "Arguments:", "Parameters:"):
            in_args = True
            current = None
            continue

        if in_args:
            # A new unindented section ends the argument block.
            if stripped and not line.startswith((" ", "\t")):
                in_args = False
                current = None
                description_parts.append(line)
                continue

            match = re.match(r"^\s*(\*{0,2}\w+)\s*(?:\([^)]*\))?\s*:\s*(.*)$", line)
            if match:
                current = match.group(1).lstrip("*")
                args[current] = match.group(2).strip()
            elif current and stripped:
                args[current] = (args[current] + " " + stripped).strip()
            continue

        description_parts.append(line)

    description = "\n".join(description_parts).strip()
    return description, args


def build_input_schema(fn: Callable) -> dict[str, Any]:
    """Derive a tools/list inputSchema from a function's signature and docstring."""
    _, arg_docs = _parse_docstring(fn.__doc__)
    signature = inspect.signature(fn)

    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, param in signature.parameters.items():
        if name in ("self", "cls") or param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue

        schema = _schema_for(param.annotation)
        if name in arg_docs:
            schema["description"] = arg_docs[name]
        properties[name] = schema

        if param.default is inspect.Parameter.empty:
            required.append(name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


class Server:
    """
    A tools-only MCP server.

    Register functions with the `tool()` decorator, then call `run()` to serve
    on stdio until the client closes the connection.
    """

    def __init__(self, name: str, version: str = "0.0.0",
                 instructions: str | None = None) -> None:
        self.name = name
        self.version = version
        self.instructions = instructions
        self.tools: dict[str, Callable] = {}
        self._schemas: dict[str, dict[str, Any]] = {}
        self._descriptions: dict[str, str] = {}

    # --- registration -------------------------------------------------------

    def tool(self, name: str | None = None) -> Callable[[Callable], Callable]:
        """
        Register a function as an MCP tool.

        The decorator returns the function untouched, so the same function
        stays directly callable -- from other tools, and from tests.
        """
        def decorator(fn: Callable) -> Callable:
            tool_name = name or fn.__name__
            description, _ = _parse_docstring(fn.__doc__)
            self.tools[tool_name] = fn
            self._schemas[tool_name] = build_input_schema(fn)
            self._descriptions[tool_name] = description
            return fn
        return decorator

    def tool_definitions(self) -> list[dict[str, Any]]:
        """The `tools` array returned by tools/list."""
        return [
            {
                "name": tool_name,
                "description": self._descriptions.get(tool_name, ""),
                "inputSchema": self._schemas[tool_name],
            }
            for tool_name in sorted(self.tools)
        ]

    # --- protocol -----------------------------------------------------------

    def negotiate_version(self, requested: Any) -> str:
        """Echo a protocol version we know, otherwise offer our latest."""
        if isinstance(requested, str) and requested in KNOWN_PROTOCOL_VERSIONS:
            return requested
        return LATEST_PROTOCOL_VERSION

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}

        if name not in self.tools:
            raise _RpcError(INVALID_PARAMS, f"Unknown tool: {name}")
        if not isinstance(arguments, dict):
            raise _RpcError(INVALID_PARAMS, "arguments must be an object")

        try:
            result = self.tools[name](**arguments)
        except TypeError as exc:
            # A signature mismatch is the client's fault, so it is a protocol
            # error rather than a tool failure.
            raise _RpcError(INVALID_PARAMS, f"Invalid arguments for {name}: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - surfaced to the model, not swallowed
            return {
                "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                "isError": True,
            }

        return {"content": [{"type": "text", "text": _as_text(result)}]}

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """
        Handle one decoded JSON-RPC message.

        Returns the response to send, or None for notifications, which by
        definition get no reply.
        """
        method = message.get("method")
        message_id = message.get("id")
        params = message.get("params") or {}
        is_notification = "id" not in message

        if not isinstance(method, str):
            if is_notification:
                return None
            return _error(message_id, INVALID_REQUEST, "Missing method")

        try:
            if method == "initialize":
                result: Any = {
                    "protocolVersion": self.negotiate_version(params.get("protocolVersion")),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": self.name, "version": self.version},
                }
                if self.instructions:
                    result["instructions"] = self.instructions
            elif method == "tools/list":
                result = {"tools": self.tool_definitions()}
            elif method == "tools/call":
                result = self._call_tool(params)
            elif method == "ping":
                result = {}
            elif method.startswith("notifications/"):
                return None
            else:
                if is_notification:
                    return None
                return _error(message_id, METHOD_NOT_FOUND, f"Unknown method: {method}")
        except _RpcError as exc:
            if is_notification:
                return None
            return _error(message_id, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001 - never let the loop die
            if is_notification:
                return None
            return _error(message_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": message_id, "result": result}

    # --- transport ----------------------------------------------------------

    def serve(self, stdin: Iterable[str], write: Callable[[str], Any]) -> None:
        """
        Run the message loop over any line source and sink.

        Kept separate from run() so tests can drive it with plain lists and
        capture what would go out on the wire.
        """
        for line in stdin:
            line = line.strip()
            if not line:
                continue

            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                write(json.dumps(_error(None, PARSE_ERROR, "Invalid JSON")))
                continue

            if not isinstance(message, dict):
                write(json.dumps(_error(None, INVALID_REQUEST, "Expected a JSON object")))
                continue

            response = self.handle(message)
            if response is not None:
                # separators keep the payload on one line, which the
                # newline-delimited stdio transport requires.
                write(json.dumps(response, separators=(",", ":")))

    def run(self) -> None:
        """Serve on stdin/stdout until the client closes the connection."""
        def write(payload: str) -> None:
            sys.stdout.write(payload + "\n")
            sys.stdout.flush()

        try:
            self.serve(sys.stdin, write)
        except KeyboardInterrupt:  # pragma: no cover
            pass


class _RpcError(Exception):
    """A protocol-level failure that must become a JSON-RPC error object."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def _as_text(result: Any) -> str:
    """
    Render a tool's return value as the text content block.

    Strings pass through unchanged; everything else is JSON so the model gets
    structure rather than a Python repr.
    """
    if isinstance(result, str):
        return result
    if result is None:
        return ""
    try:
        return json.dumps(result, indent=2, default=str)
    except (TypeError, ValueError):
        return str(result)
