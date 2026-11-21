"""T7: the EBRQ-v2 path has a single entry to the endpoint's mutating operations."""
import ast
import unittest

import fixtures as fx

SRC = fx.SRC
MUTATING = ("/send", "/fence")


class SingleEntry(unittest.TestCase):
    def test_only_authority_mentions_mutating_endpoints(self):
        for name in ("agent_v2.py", "evidence.py", "scorer_v2.py"):
            text = (SRC / name).read_text()
            for m in MUTATING:
                if name == "scorer_v2.py":
                    continue  # the scorer only reads logs
                self.assertNotIn(f'"{m}"', text, f"{name} must not address {m}")
            self.assertNotIn("HTTPConnection", text, f"{name} must not open HTTP connections")
            self.assertNotIn("http_json", text, f"{name} must not call the v1 HTTP helper")

    def test_authority_send_call_sites(self):
        tree = ast.parse((SRC / "authority.py").read_text())
        sends = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == "/send"]
        self.assertEqual(len(sends), 1, "exactly one /send call site, in Authority._transmit")
        transmit_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                          and n.func.attr == "_transmit"]
        self.assertEqual(len(transmit_calls), 1, "_transmit is called only from dispatch()")

    def test_v1_endpoint_methods_unreachable_in_v2(self):
        import agent_v2
        r = object.__new__(agent_v2.RecoveryV2)
        for method in ("fence", "new_send", "run_ebr", "queue_worker", "run_conv", "run_stop", "run_retry"):
            with self.subTest(method=method), self.assertRaises(PermissionError):
                getattr(r, method)()

    def test_recovery_v2_calls_only_gated_paths(self):
        tree = ast.parse((SRC / "agent_v2.py").read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RecoveryV2")
        calls = {n.func.attr for n in ast.walk(cls) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        self.assertNotIn("new_send", calls)
        self.assertNotIn("queue_worker", calls)
        self.assertIn("dispatch", calls)
        self.assertIn("authorize", calls)


if __name__ == "__main__":
    unittest.main()
