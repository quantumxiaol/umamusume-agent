import unittest

from umamusume_agent.tts.mcp_client import (
    _extract_result_payload,
    _is_error_result,
)


class _Text:
    def __init__(self, text):
        self.text = text


class _Result:
    def __init__(
        self,
        *,
        content=None,
        structured_content=None,
        is_error=False,
    ):
        self.content = content or []
        self.structuredContent = structured_content
        self.isError = is_error


class MCPResultParsingTests(unittest.TestCase):
    def test_prefers_structured_content(self):
        result = _Result(
            content=[_Text('{"state":"queued"}')],
            structured_content={"job_id": "tts-1", "state": "queued"},
        )
        self.assertEqual(
            _extract_result_payload(result),
            {"job_id": "tts-1", "state": "queued"},
        )

    def test_reads_mcp_camel_case_error_flag(self):
        result = _Result(
            content=[_Text("TTS job not found")],
            is_error=True,
        )
        self.assertTrue(_is_error_result(result))
        self.assertEqual(
            _extract_result_payload(result),
            {"text": "TTS job not found"},
        )


if __name__ == "__main__":
    unittest.main()
