"""Static regression checks for passive Bruce signal scanning (stdlib-only)."""
import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def read_tree(filename: str):
    return ast.parse((ROOT / filename).read_text(encoding="utf-8"))


class PassiveScanTests(unittest.TestCase):
    def test_transport_allows_scan_operations(self):
        module = read_tree("mqtt_transport.py")
        assignment = next(node for node in module.body if isinstance(node, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == "ALLOWED_OPS"
                                  for t in node.targets))
        allowed = ast.literal_eval(assignment.value.args[0])
        self.assertTrue({"wifi.scan", "ble.scan"}.issubset(allowed))

    def test_separate_passive_tools(self):
        module = read_tree("mcp_server.py")
        for function, expected_op in (("bruce_wifi_scan", "wifi.scan"),
                                      ("bruce_ble_scan", "ble.scan")):
            with self.subTest(tool=function):
                node = next(x for x in module.body
                            if isinstance(x, ast.AsyncFunctionDef) and x.name == function)
                self.assertTrue(node.decorator_list, "MCP tool must be registered")
                calls = [n for n in ast.walk(node)
                         if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                         and n.func.id == "command"]
                self.assertEqual(len(calls), 1)
                self.assertEqual(ast.literal_eval(calls[0].args[0]), expected_op)
                self.assertFalse(any(k.arg == "args" for k in calls[0].keywords),
                                 "Passive tools must not accept arbitrary command arguments")


if __name__ == "__main__":
    unittest.main()
