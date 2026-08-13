"""Tests for the dependency-free MCP stdio implementation."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mcp_stdio  # noqa: E402
from mcp_stdio import Server  # noqa: E402


def build_server() -> Server:
    server = Server("test-server", "1.2.3", instructions="Do the thing.")

    @server.tool()
    def greet(name: str, excited: bool = False) -> str:
        """
        Greet someone by name.

        Args:
            name: Who to greet.
            excited: Whether to shout it.
        """
        return f"HELLO {name}" if excited else f"Hello {name}"

    @server.tool()
    def add(a: int, b: int) -> dict:
        """Add two numbers."""
        return {"sum": a + b}

    @server.tool()
    def explode() -> str:
        """Always fails."""
        raise RuntimeError("boom")

    return server


class TestDocstringParsing(unittest.TestCase):
    def test_splits_description_and_arguments(self):
        doc = """
        Short summary.

        More detail here.

        Args:
            first: The first thing.
            second: The second thing.
        """
        description, args = mcp_stdio._parse_docstring(doc)

        self.assertIn("Short summary.", description)
        self.assertIn("More detail here.", description)
        self.assertNotIn("first", description)
        self.assertEqual(args["first"], "The first thing.")
        self.assertEqual(args["second"], "The second thing.")

    def test_continuation_lines_join_the_previous_argument(self):
        doc = """
        Summary.

        Args:
            thing: A description that
                wraps onto a second line.
        """
        _, args = mcp_stdio._parse_docstring(doc)
        self.assertEqual(args["thing"], "A description that wraps onto a second line.")

    def test_section_after_args_returns_to_the_description(self):
        """Examples help the model call the tool, so they belong in the description."""
        doc = """
        Summary.

        Args:
            thing: Something.

        Examples:
            tool("value")
        """
        description, args = mcp_stdio._parse_docstring(doc)

        self.assertIn("Examples:", description)
        self.assertIn('tool("value")', description)
        self.assertEqual(list(args), ["thing"])

    def test_type_annotations_in_docstring_are_stripped(self):
        doc = """
        Summary.

        Args:
            count (int): How many.
        """
        _, args = mcp_stdio._parse_docstring(doc)
        self.assertEqual(args["count"], "How many.")

    def test_no_docstring(self):
        self.assertEqual(mcp_stdio._parse_docstring(None), ("", {}))


class TestSchemaBuilding(unittest.TestCase):
    def test_maps_basic_types(self):
        def fn(a: str, b: int, c: float, d: bool, e: dict, f: list):
            """Summary."""

        properties = mcp_stdio.build_input_schema(fn)["properties"]

        self.assertEqual(properties["a"], {"type": "string"})
        self.assertEqual(properties["b"], {"type": "integer"})
        self.assertEqual(properties["c"], {"type": "number"})
        self.assertEqual(properties["d"], {"type": "boolean"})
        self.assertEqual(properties["e"], {"type": "object"})
        self.assertEqual(properties["f"], {"type": "array"})

    def test_optional_annotations_keep_their_base_type(self):
        """`dict | None` is a syntax error at runtime on 3.9, so it is parsed as text."""
        def fn(a: "dict | None" = None, b: "Optional[int]" = None):
            """Summary."""

        properties = mcp_stdio.build_input_schema(fn)["properties"]

        self.assertEqual(properties["a"], {"type": "object"})
        self.assertEqual(properties["b"], {"type": "integer"})

    def test_subscripted_generics_keep_the_container_type(self):
        def fn(a: "list[str]", b: "dict[str, Any]"):
            """Summary."""

        properties = mcp_stdio.build_input_schema(fn)["properties"]

        self.assertEqual(properties["a"], {"type": "array"})
        self.assertEqual(properties["b"], {"type": "object"})

    def test_unknown_annotation_yields_an_unconstrained_schema(self):
        """A wrong constraint would block a valid call; no constraint just validates less."""
        def fn(a: "SomeCustomType", b):
            """Summary."""

        properties = mcp_stdio.build_input_schema(fn)["properties"]

        self.assertEqual(properties["a"], {})
        self.assertEqual(properties["b"], {})

    def test_parameters_without_defaults_are_required(self):
        def fn(a: str, b: int = 3):
            """Summary."""

        schema = mcp_stdio.build_input_schema(fn)

        self.assertEqual(schema["required"], ["a"])

    def test_no_required_key_when_everything_is_optional(self):
        def fn(a: int = 1):
            """Summary."""

        self.assertNotIn("required", mcp_stdio.build_input_schema(fn))

    def test_argument_descriptions_come_from_the_docstring(self):
        server = build_server()
        schema = server._schemas["greet"]

        self.assertEqual(schema["properties"]["name"]["description"], "Who to greet.")
        self.assertEqual(schema["properties"]["excited"]["description"], "Whether to shout it.")

    def test_varargs_are_skipped(self):
        def fn(a: str, *args, **kwargs):
            """Summary."""

        self.assertEqual(list(mcp_stdio.build_input_schema(fn)["properties"]), ["a"])


class TestInitialize(unittest.TestCase):
    def setUp(self):
        self.server = build_server()

    def _initialize(self, version):
        return self.server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": version, "capabilities": {},
                       "clientInfo": {"name": "c", "version": "1"}},
        })

    def test_known_version_is_echoed_back(self):
        for version in mcp_stdio.KNOWN_PROTOCOL_VERSIONS:
            result = self._initialize(version)["result"]
            self.assertEqual(result["protocolVersion"], version)

    def test_unknown_version_falls_back_to_latest(self):
        """The lifecycle spec says to answer with a version we do support."""
        result = self._initialize("1999-01-01")["result"]
        self.assertEqual(result["protocolVersion"], mcp_stdio.LATEST_PROTOCOL_VERSION)

    def test_missing_version_falls_back_to_latest(self):
        result = self.server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
        })["result"]
        self.assertEqual(result["protocolVersion"], mcp_stdio.LATEST_PROTOCOL_VERSION)

    def test_advertises_only_the_tools_capability(self):
        result = self._initialize("2025-06-18")["result"]
        self.assertEqual(list(result["capabilities"]), ["tools"])

    def test_reports_server_identity(self):
        result = self._initialize("2025-06-18")["result"]
        self.assertEqual(result["serverInfo"], {"name": "test-server", "version": "1.2.3"})
        self.assertEqual(result["instructions"], "Do the thing.")

    def test_instructions_are_omitted_when_not_set(self):
        server = Server("bare")
        result = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})["result"]
        self.assertNotIn("instructions", result)


class TestToolsList(unittest.TestCase):
    def setUp(self):
        self.server = build_server()
        self.tools = self.server.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )["result"]["tools"]

    def test_lists_every_registered_tool(self):
        self.assertEqual([t["name"] for t in self.tools], ["add", "explode", "greet"])

    def test_each_tool_carries_a_description_and_schema(self):
        by_name = {t["name"]: t for t in self.tools}

        self.assertEqual(by_name["add"]["description"], "Add two numbers.")
        self.assertEqual(by_name["add"]["inputSchema"]["type"], "object")
        self.assertEqual(by_name["add"]["inputSchema"]["required"], ["a", "b"])


class TestToolsCall(unittest.TestCase):
    def setUp(self):
        self.server = build_server()

    def _call(self, name, arguments=None):
        return self.server.handle({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })

    def test_returns_text_content(self):
        result = self._call("greet", {"name": "Ada"})["result"]

        self.assertEqual(result["content"], [{"type": "text", "text": "Hello Ada"}])
        self.assertNotIn("isError", result)

    def test_defaults_are_applied(self):
        result = self._call("greet", {"name": "Ada", "excited": True})["result"]
        self.assertEqual(result["content"][0]["text"], "HELLO Ada")

    def test_non_string_results_are_json_encoded(self):
        result = self._call("add", {"a": 2, "b": 3})["result"]
        self.assertEqual(json.loads(result["content"][0]["text"]), {"sum": 5})

    def test_a_failing_tool_reports_is_error_not_a_protocol_error(self):
        """
        The spec reserves JSON-RPC errors for protocol problems; a tool that
        raises should come back as content the model can read and react to.
        """
        response = self._call("explode")

        self.assertNotIn("error", response)
        self.assertTrue(response["result"]["isError"])
        self.assertIn("boom", response["result"]["content"][0]["text"])

    def test_unknown_tool_is_a_protocol_error(self):
        response = self._call("nope")

        self.assertEqual(response["error"]["code"], mcp_stdio.INVALID_PARAMS)
        self.assertIn("Unknown tool", response["error"]["message"])

    def test_wrong_arguments_are_a_protocol_error(self):
        response = self._call("greet", {"wrong": 1})

        self.assertEqual(response["error"]["code"], mcp_stdio.INVALID_PARAMS)
        self.assertIn("Invalid arguments", response["error"]["message"])

    def test_non_object_arguments_are_rejected(self):
        response = self.server.handle({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "greet", "arguments": ["Ada"]},
        })
        self.assertEqual(response["error"]["code"], mcp_stdio.INVALID_PARAMS)


class TestOtherMethods(unittest.TestCase):
    def setUp(self):
        self.server = build_server()

    def test_ping_returns_an_empty_result(self):
        response = self.server.handle({"jsonrpc": "2.0", "id": 9, "method": "ping"})
        self.assertEqual(response["result"], {})

    def test_notifications_get_no_response(self):
        self.assertIsNone(
            self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )

    def test_unknown_method_is_method_not_found(self):
        response = self.server.handle({"jsonrpc": "2.0", "id": 4, "method": "resources/list"})
        self.assertEqual(response["error"]["code"], mcp_stdio.METHOD_NOT_FOUND)

    def test_unknown_notification_is_silently_ignored(self):
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "whatever"}))

    def test_missing_method_is_an_invalid_request(self):
        response = self.server.handle({"jsonrpc": "2.0", "id": 5})
        self.assertEqual(response["error"]["code"], mcp_stdio.INVALID_REQUEST)


class TestServeLoop(unittest.TestCase):
    def _run(self, lines):
        server = build_server()
        written: list[str] = []
        server.serve(iter(lines), written.append)
        return written

    def test_processes_a_full_session(self):
        written = self._run([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "add", "arguments": {"a": 1, "b": 1}}}),
        ])

        # Three requests, one notification -> three responses.
        self.assertEqual(len(written), 3)
        self.assertEqual([json.loads(w)["id"] for w in written], [1, 2, 3])

    def test_every_message_is_a_single_line(self):
        """The stdio transport is newline delimited, so payloads may not wrap."""
        written = self._run([
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "add", "arguments": {"a": 1, "b": 1}}}),
        ])

        for payload in written:
            self.assertNotIn("\n", payload)

    def test_blank_lines_are_skipped(self):
        self.assertEqual(self._run(["", "   ", "\n"]), [])

    def test_malformed_json_gets_a_parse_error(self):
        written = self._run(["{not json"])

        error = json.loads(written[0])
        self.assertEqual(error["error"]["code"], mcp_stdio.PARSE_ERROR)
        self.assertIsNone(error["id"])

    def test_non_object_message_is_an_invalid_request(self):
        written = self._run(["[1, 2, 3]"])
        self.assertEqual(json.loads(written[0])["error"]["code"], mcp_stdio.INVALID_REQUEST)

    def test_a_bad_message_does_not_end_the_session(self):
        written = self._run([
            "{not json",
            json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}),
        ])

        self.assertEqual(len(written), 2)
        self.assertEqual(json.loads(written[1])["result"], {})


class TestAsText(unittest.TestCase):
    def test_strings_pass_through(self):
        self.assertEqual(mcp_stdio._as_text("plain"), "plain")

    def test_none_becomes_empty(self):
        self.assertEqual(mcp_stdio._as_text(None), "")

    def test_structures_become_json(self):
        self.assertEqual(json.loads(mcp_stdio._as_text([1, {"a": 2}])), [1, {"a": 2}])

    def test_unserialisable_values_fall_back_to_str(self):
        self.assertIn("object", mcp_stdio._as_text(object()))


if __name__ == "__main__":
    unittest.main()
