import ast
from pathlib import Path
import unittest

class ContractTests(unittest.TestCase):
    def test_gateway_is_explicitly_allowlisted(self):
        src=(Path(__file__).resolve().parents[1]/"gateway/server.py").read_text()
        ast.parse(src)
        self.assertIn("OPS = frozenset",src)
        self.assertIn("retain=False",src)
        self.assertIn("tls_set(",src)
        self.assertNotIn("set_insecure",src)
    def test_build_overlay_is_syntax_valid(self):
        ast.parse((Path(__file__).resolve().parents[1]/"apply_to_upstream.py").read_text())

if __name__=="__main__":
    unittest.main()
