import unittest
from types import SimpleNamespace

from src.envstate.llm_response import response_text


def _resp(message):
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class ResponseTextTests(unittest.TestCase):
    def test_returns_content_when_present(self):
        resp = _resp(SimpleNamespace(content="Action: ls", reasoning="ignored"))
        self.assertEqual(response_text(resp), "Action: ls")

    def test_falls_back_to_reasoning_attr_when_content_none(self):
        resp = _resp(SimpleNamespace(content=None, reasoning="Action: pip install x"))
        self.assertEqual(response_text(resp), "Action: pip install x")

    def test_falls_back_to_reasoning_attr_when_content_empty(self):
        resp = _resp(SimpleNamespace(content="", reasoning="Action: pip install x"))
        self.assertEqual(response_text(resp), "Action: pip install x")

    def test_falls_back_to_model_extra_reasoning(self):
        msg = SimpleNamespace(content="", model_extra={"reasoning": "Action: make"})
        self.assertEqual(response_text(_resp(msg)), "Action: make")

    def test_returns_empty_when_all_missing(self):
        msg = SimpleNamespace(content=None)
        self.assertEqual(response_text(_resp(msg)), "")

    def test_returns_empty_on_malformed_response(self):
        self.assertEqual(response_text(SimpleNamespace(choices=[])), "")
        self.assertEqual(response_text(SimpleNamespace()), "")
        self.assertEqual(response_text(None), "")


if __name__ == "__main__":
    unittest.main()
