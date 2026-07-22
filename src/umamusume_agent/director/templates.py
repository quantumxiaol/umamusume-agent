"""Loading of versioned, data-only scene templates."""

from __future__ import annotations

import json
from pathlib import Path

from .models import SceneTemplate


class SceneTemplateRepository:
    def __init__(self, template_dir: str | Path):
        self.template_dir = Path(template_dir)

    def list(self) -> list[SceneTemplate]:
        templates: list[SceneTemplate] = []
        if not self.template_dir.exists():
            return templates
        for path in sorted(self.template_dir.glob("*.json")):
            with path.open("r", encoding="utf-8") as file:
                templates.append(SceneTemplate.model_validate(json.load(file)))
        return templates

    def get(self, template_id: str) -> SceneTemplate:
        for template in self.list():
            if template.template_id == template_id:
                return template
        raise FileNotFoundError(f"Scene template not found: {template_id}")
