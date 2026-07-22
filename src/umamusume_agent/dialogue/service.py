"""Application service for the existing single-character dialogue flow."""

from __future__ import annotations

from typing import Any

from .context import LegacyDialogueContextBuilder
from .models import DialogueTurnResult
from .protocol import structured_reply_message
from .runtime import CharacterRuntime


class DialogueService:
    """Execute one complete legacy dialogue turn without HTTP concerns."""

    def __init__(
        self,
        *,
        runtime: CharacterRuntime,
        context_builder: LegacyDialogueContextBuilder,
    ):
        self.runtime = runtime
        self.context_builder = context_builder

    async def execute_turn(
        self,
        *,
        session: Any,
        message: str,
        text_only: bool = False,
    ) -> DialogueTurnResult:
        session.add_message("user", message)

        context = self.context_builder.build(
            character=session.character,
            history=session.history,
            text_only=text_only,
        )
        reply = await self.runtime.generate_reply(context)

        session.add_message(
            "assistant",
            reply.dialogue,
            action=reply.action,
            dialogue=reply.dialogue,
            source_format=reply.source_format,
            schema_version=reply.schema_version,
        )

        return DialogueTurnResult(
            reply=reply,
            message=structured_reply_message(reply),
        )

