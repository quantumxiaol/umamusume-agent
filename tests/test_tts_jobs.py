import asyncio
import copy
import tempfile
import unittest
from pathlib import Path

from umamusume_agent.tts.agent import JapaneseDialoguePreparer
from umamusume_agent.tts.jobs import TTSJobManager, TTSJobNotFound
from umamusume_agent.tts.models import (
    PreparedJapaneseDialogue,
    TTSCharacterProfile,
    TTSContextEvent,
    TTSSubmitRequest,
)


class _Preparer:
    def __init__(self):
        self.requests = []

    async def prepare(self, request):
        self.requests.append(request)
        return PreparedJapaneseDialogue(
            subtitle_ja="お兄さま、行きましょう。",
            spoken_text_ja="お兄さま、行きましょう。",
            fish_text="お兄さま、行きましょう。",
        )


class _FishClient:
    def __init__(self):
        self.calls = []

    async def voice_clone(self, **kwargs):
        self.calls.append(kwargs)
        callback = kwargs.get("on_download_start")
        if callback:
            callback()
        kwargs["destination"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["destination"].write_bytes(b"RIFF-test-audio")
        return {"audio_path": str(kwargs["destination"])}


class _TranslationCompletions:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        message = type(
            "_Message",
            (),
            {
                "content": (
                    '{"subtitle_ja":"お兄さま、行きましょう。",'
                    '"spoken_text_ja":"お兄さま、行きましょう。"}'
                )
            },
        )()
        choice = type("_Choice", (), {"message": message})()
        return type("_Response", (), {"choices": [choice]})()


class _TranslationClient:
    def __init__(self):
        self.completions = _TranslationCompletions()
        self.chat = type("_Chat", (), {"completions": self.completions})()


class _SequenceTranslationCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        item = self.responses.pop(0)
        message = type(
            "_Message",
            (),
            {"content": item.get("content")},
        )()
        choice = type(
            "_Choice",
            (),
            {
                "message": message,
                "finish_reason": item.get("finish_reason", "stop"),
            },
        )()
        return type("_Response", (), {"choices": [choice]})()


class _SequenceTranslationClient:
    def __init__(self, responses):
        self.completions = _SequenceTranslationCompletions(responses)
        self.chat = type("_Chat", (), {"completions": self.completions})()


class TTSJobManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.reference_audio = root / "reference.wav"
        self.reference_audio.write_bytes(b"reference")
        self.preparer = _Preparer()
        self.fish = _FishClient()
        self.manager = TTSJobManager(
            preparer=self.preparer,
            fish_client=self.fish,
            outputs_dir=root / "outputs",
            max_concurrent_jobs=1,
            audio_format="wav",
            speaker_prefix="<|speaker:0|>",
            fish_generation_options={"temperature": 0.7},
        )

    async def asyncTearDown(self):
        await self.manager.close()
        self.temp_dir.cleanup()

    def request(self, utterance_id="utterance-1"):
        return TTSSubmitRequest(
            user_uuid="00000000-0000-4000-8000-000000000001",
            source_session_id="session-1",
            utterance_id=utterance_id,
            subtitle_zh="哥哥，我们走吧。",
            speaker=TTSCharacterProfile(
                actor_id="rice_shower",
                name_zh="米浴",
                name_jp="ライスシャワー",
                first_person="ライス",
                user_address="お兄さま",
                reference_audio_path=str(self.reference_audio),
                reference_text_ja="ライス、頑張ります。",
            ),
            target_actor_ids=["player"],
            context_events=[
                TTSContextEvent(
                    event_id="event-1",
                    actor_id="player",
                    actor_type="trainer",
                    display_name="训练员",
                    event_type="dialogue",
                    content="我们回去吧。",
                )
            ],
        )

    async def test_submit_returns_immediately_and_finishes_in_background(self):
        snapshot = await self.manager.submit(self.request())
        self.assertEqual(snapshot.state, "queued")

        for _ in range(20):
            current = await self.manager.get(
                job_id=snapshot.job_id,
                user_uuid=snapshot.user_uuid,
            )
            if current.state == "ready":
                break
            await asyncio.sleep(0)

        self.assertEqual(current.state, "ready")
        self.assertTrue(Path(current.audio_path).is_file())
        self.assertEqual(len(self.preparer.requests), 1)
        self.assertEqual(
            self.fish.calls[0]["text"],
            "<|speaker:0|>お兄さま、行きましょう。",
        )

    async def test_same_utterance_is_idempotent_and_owner_is_enforced(self):
        first = await self.manager.submit(self.request())
        second = await self.manager.submit(self.request())
        self.assertEqual(first.job_id, second.job_id)
        for _ in range(20):
            current = await self.manager.get(
                job_id=first.job_id,
                user_uuid=first.user_uuid,
            )
            if current.state == "ready":
                break
            await asyncio.sleep(0)

        with self.assertRaises(TTSJobNotFound):
            await self.manager.get(
                job_id=first.job_id,
                user_uuid="00000000-0000-4000-8000-000000000099",
            )


class JapaneseDialoguePreparerTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def request(subtitle="哥哥，我们走吧。"):
        return TTSSubmitRequest(
            user_uuid="00000000-0000-4000-8000-000000000001",
            source_session_id="scene-1",
            utterance_id="utterance-1",
            subtitle_zh=subtitle,
            speaker=TTSCharacterProfile(
                actor_id="rice_shower",
                name_zh="米浴",
                name_jp="ライスシャワー",
                reference_audio_path="/tmp/reference.wav",
                first_person="ライス",
                user_address="お兄さま",
            ),
        )

    @staticmethod
    def valid_json():
        return (
            '{"subtitle_ja":"お兄さま、行きましょう。",'
            '"spoken_text_ja":"お兄さま、行きましょう。"}'
        )

    async def test_empty_json_mode_content_retries_prompt_only(self):
        client = _SequenceTranslationClient(
            [
                {"content": "", "finish_reason": "stop"},
                {
                    "content": self.valid_json(),
                    "finish_reason": "stop",
                },
            ]
        )
        preparer = JapaneseDialoguePreparer(
            client=client,
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com",
            temperature=0.2,
            max_tokens=1024,
            prefix_cache_enabled=True,
            content_retries=2,
        )

        prepared = await preparer.prepare(self.request())

        self.assertEqual(
            prepared.spoken_text_ja,
            "お兄さま、行きましょう。",
        )
        self.assertEqual(len(client.completions.calls), 2)
        self.assertIn(
            "response_format",
            client.completions.calls[0],
        )
        self.assertNotIn(
            "response_format",
            client.completions.calls[1],
        )

    async def test_length_response_retries_with_larger_budget(self):
        client = _SequenceTranslationClient(
            [
                {
                    "content": '{"subtitle_ja":"途中',
                    "finish_reason": "length",
                },
                {
                    "content": self.valid_json(),
                    "finish_reason": "stop",
                },
            ]
        )
        preparer = JapaneseDialoguePreparer(
            client=client,
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com",
            temperature=0.2,
            max_tokens=1024,
            prefix_cache_enabled=True,
            content_retries=2,
        )

        prepared = await preparer.prepare(self.request())

        self.assertEqual(
            prepared.subtitle_ja,
            "お兄さま、行きましょう。",
        )
        self.assertEqual(
            client.completions.calls[0]["max_tokens"],
            1024,
        )
        self.assertEqual(
            client.completions.calls[1]["max_tokens"],
            2048,
        )
        self.assertIn(
            "response_format",
            client.completions.calls[1],
        )

    async def test_malformed_json_uses_bounded_repair_turns(self):
        client = _SequenceTranslationClient(
            [
                {
                    "content": '{"subtitle_ja":"途中',
                    "finish_reason": "stop",
                },
                {
                    "content": '{"subtitle_ja":"まだ途中',
                    "finish_reason": "stop",
                },
                {
                    "content": self.valid_json(),
                    "finish_reason": "stop",
                },
            ]
        )
        preparer = JapaneseDialoguePreparer(
            client=client,
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com",
            temperature=0.2,
            max_tokens=1024,
            prefix_cache_enabled=True,
            repair_attempts=2,
        )

        prepared = await preparer.prepare(self.request())

        self.assertEqual(
            prepared.spoken_text_ja,
            "お兄さま、行きましょう。",
        )
        self.assertEqual(len(client.completions.calls), 3)
        repair_messages = client.completions.calls[2]["messages"]
        self.assertTrue(
            any(
                "上一条输出不符合固定格式" in str(item["content"])
                for item in repair_messages
            )
        )

    async def test_failed_translation_does_not_pollute_next_prefix(self):
        client = _SequenceTranslationClient(
            [
                {"content": "", "finish_reason": "stop"},
                {"content": "", "finish_reason": "stop"},
                {
                    "content": self.valid_json(),
                    "finish_reason": "stop",
                },
            ]
        )
        preparer = JapaneseDialoguePreparer(
            client=client,
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com",
            temperature=0.2,
            max_tokens=1024,
            prefix_cache_enabled=True,
            content_retries=1,
        )

        with self.assertRaisesRegex(ValueError, "no usable content"):
            await preparer.prepare(self.request("第一次失败。"))

        prepared = await preparer.prepare(
            self.request("第二次成功。").model_copy(
                update={"utterance_id": "utterance-2"}
            )
        )

        self.assertEqual(
            prepared.spoken_text_ja,
            "お兄さま、行きましょう。",
        )
        successful_messages = client.completions.calls[2]["messages"]
        rendered = "\n".join(
            str(message["content"]) for message in successful_messages
        )
        self.assertNotIn("第一次失败。", rendered)
        self.assertIn("第二次成功。", rendered)

    async def test_thread_is_append_only_for_prefix_cache(self):
        client = _TranslationClient()
        preparer = JapaneseDialoguePreparer(
            client=client,
            model="qwen-test",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            temperature=0.2,
            max_tokens=400,
            prefix_cache_enabled=True,
        )
        reference = Path("/tmp/reference.wav")
        profile = TTSCharacterProfile(
            actor_id="rice_shower",
            name_zh="米浴",
            name_jp="ライスシャワー",
            reference_audio_path=str(reference),
            first_person="ライス",
            user_address="お兄さま",
        )
        first = TTSSubmitRequest(
            user_uuid="00000000-0000-4000-8000-000000000001",
            source_session_id="scene-1",
            utterance_id="utterance-1",
            subtitle_zh="哥哥，我们走吧。",
            speaker=profile,
            context_events=[
                TTSContextEvent(
                    event_id="event-1",
                    display_name="训练员",
                    event_type="dialogue",
                    content="我们回去吧。",
                )
            ],
        )
        await preparer.prepare(first)

        second = first.model_copy(
            update={
                "utterance_id": "utterance-2",
                "subtitle_zh": "嗯，米浴准备好了。",
                "context_events": [
                    *first.context_events,
                    TTSContextEvent(
                        event_id="event-2",
                        display_name="环境",
                        event_type="scene_event",
                        content="夜幕已经降临。",
                    ),
                ],
            }
        )
        await preparer.prepare(second)

        first_messages = client.completions.calls[0]["messages"]
        second_messages = client.completions.calls[1]["messages"]
        self.assertEqual(
            second_messages[: len(first_messages)],
            first_messages,
        )
        self.assertEqual(
            second_messages[0]["cache_control"],
            {"type": "ephemeral"},
        )
        system_prompt = str(second_messages[0]["content"])
        self.assertIn(
            "当前中文字幕中明确出现的称呼 > 角色完整人设",
            system_prompt,
        )
        self.assertIn(
            "必须忽略旧字段",
            system_prompt,
        )
        rendered = "\n".join(
            str(message["content"]) for message in second_messages
        )
        self.assertEqual(rendered.count("我们回去吧。"), 1)
        self.assertIn("夜幕已经降临。", rendered)

    async def test_history_divergence_rebuilds_dynamic_translation_context(self):
        client = _TranslationClient()
        preparer = JapaneseDialoguePreparer(
            client=client,
            model="qwen-test",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            temperature=0.2,
            max_tokens=400,
            prefix_cache_enabled=True,
        )
        profile = TTSCharacterProfile(
            actor_id="rice_shower",
            name_zh="米浴",
            name_jp="ライスシャワー",
            reference_audio_path="/tmp/reference.wav",
        )
        first = TTSSubmitRequest(
            user_uuid="00000000-0000-4000-8000-000000000001",
            source_session_id="scene-1",
            utterance_id="utterance-1",
            subtitle_zh="原来的对白。",
            speaker=profile,
            context_events=[
                TTSContextEvent(
                    event_id="event-1",
                    display_name="训练员",
                    event_type="dialogue",
                    content="原来的上下文。",
                )
            ],
        )
        await preparer.prepare(first)

        edited = first.model_copy(
            update={
                "utterance_id": "utterance-2",
                "subtitle_zh": "编辑后的对白。",
                "context_events": [
                    TTSContextEvent(
                        event_id="event-1",
                        display_name="训练员",
                        event_type="dialogue",
                        content="编辑后的上下文。",
                    )
                ],
            }
        )
        await preparer.prepare(edited)

        second_messages = client.completions.calls[1]["messages"]
        rendered = "\n".join(
            str(message["content"]) for message in second_messages
        )
        self.assertNotIn("原来的上下文。", rendered)
        self.assertIn("编辑后的上下文。", rendered)
        self.assertEqual(
            client.completions.calls[0]["messages"][0],
            second_messages[0],
        )


if __name__ == "__main__":
    unittest.main()
