import unittest
from types import SimpleNamespace

from umamusume_agent.llm_usage import (
    DeepSeekUsageTracker,
    is_deepseek_base_url,
)


def _response(
    *,
    request_id: str,
    prompt: int,
    cached: int,
    completion: int,
    reasoning: int,
):
    return SimpleNamespace(
        id=request_id,
        model="deepseek-v4-flash",
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            prompt_cache_hit_tokens=cached,
            prompt_cache_miss_tokens=prompt - cached,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            completion_tokens_details=SimpleNamespace(
                reasoning_tokens=reasoning,
            ),
        ),
    )


class DeepSeekUsageTrackerTests(unittest.TestCase):
    def test_provider_detection_uses_hostname_not_substring(self):
        self.assertTrue(is_deepseek_base_url("https://api.deepseek.com"))
        self.assertTrue(is_deepseek_base_url("https://api.deepseek.com/v1"))
        self.assertFalse(
            is_deepseek_base_url("https://deepseek.com.attacker.example/v1")
        )
        self.assertFalse(
            is_deepseek_base_url(
                "https://dashscope.aliyuncs.com/compatible-mode/v1"
            )
        )

    def test_records_exact_usage_and_groups_one_browser_operation(self):
        tracker = DeepSeekUsageTracker(
            base_url="https://api.deepseek.com",
            max_events=10,
            recent_operations=3,
        )
        user_uuid = "00000000-0000-4000-8000-000000000001"

        with tracker.operation(user_uuid=user_uuid, feature="director_turn"):
            tracker.record_response(
                _response(
                    request_id="director",
                    prompt=1000,
                    cached=800,
                    completion=300,
                    reasoning=200,
                ),
                finish_reason="length",
                latency_ms=1200,
            )
            tracker.record_response(
                _response(
                    request_id="character",
                    prompt=2000,
                    cached=1900,
                    completion=200,
                    reasoning=50,
                ),
                finish_reason="stop",
                latency_ms=800,
            )

        snapshot = tracker.snapshot(user_uuid=user_uuid)
        self.assertTrue(snapshot["enabled"])
        self.assertEqual(snapshot["instance"]["request_count"], 2)
        self.assertEqual(snapshot["instance"]["prompt_tokens"], 3000)
        self.assertEqual(snapshot["instance"]["cached_input_tokens"], 2700)
        self.assertEqual(snapshot["instance"]["uncached_input_tokens"], 300)
        self.assertEqual(snapshot["instance"]["completion_tokens"], 500)
        self.assertEqual(snapshot["instance"]["reasoning_tokens"], 250)
        self.assertEqual(snapshot["instance"]["length_response_count"], 1)
        self.assertEqual(snapshot["instance"]["cache_hit_rate"], 0.9)
        self.assertEqual(len(snapshot["recent_operations"]), 1)
        self.assertEqual(
            snapshot["recent_operations"][0]["feature"],
            "director_turn",
        )
        self.assertEqual(snapshot["recent_operations"][0]["latency_ms"], 2000)

    def test_unscoped_and_other_browser_usage_is_not_exposed(self):
        tracker = DeepSeekUsageTracker(base_url="https://api.deepseek.com")
        response = _response(
            request_id="unscoped",
            prompt=100,
            cached=64,
            completion=20,
            reasoning=10,
        )
        tracker.record_response(response, finish_reason="stop")

        with tracker.operation(user_uuid="browser-a", feature="dialogue_turn"):
            tracker.record_response(response, finish_reason="stop")

        self.assertEqual(
            tracker.snapshot(user_uuid="browser-a")["instance"]["request_count"],
            1,
        )
        self.assertEqual(
            tracker.snapshot(user_uuid="browser-b")["instance"]["request_count"],
            0,
        )

    def test_non_deepseek_provider_is_disabled(self):
        tracker = DeepSeekUsageTracker(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        with tracker.operation(user_uuid="browser-a", feature="dialogue_turn"):
            tracker.record_response(
                _response(
                    request_id="ignored",
                    prompt=100,
                    cached=0,
                    completion=20,
                    reasoning=0,
                ),
                finish_reason="stop",
            )
        self.assertEqual(
            tracker.snapshot(user_uuid="browser-a"),
            {
                "enabled": False,
                "provider": "",
                "scope": "current_backend_instance",
            },
        )


if __name__ == "__main__":
    unittest.main()
