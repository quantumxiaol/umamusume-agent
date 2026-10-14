"""Offline checks against the actual character configs consumed by the runtime."""
import json
import re
from pathlib import Path

import pytest

from umamusume_agent.character.model import CharacterConfig

ROOT = Path(__file__).resolve().parents[1] / 'characters'
CONFIGS = sorted(ROOT.glob('*/config.json'))


def test_library_is_present_and_ids_are_unique():
    assert CONFIGS, 'No character library found'
    ids = [json.loads(path.read_text(encoding='utf-8'))['id'] for path in CONFIGS]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize('path', CONFIGS, ids=lambda path: path.parent.name)
def test_runtime_character_prompt_and_reference_bundle(path):
    config = CharacterConfig.model_validate_json(path.read_text(encoding='utf-8'))
    config.character_dir = path.parent
    prompt = (path.parent / 'prompt.md').read_text(encoding='utf-8').strip()
    assert config.get_system_prompt() == prompt
    assert prompt.startswith('# 角色扮演提示词：')
    assert '扮演主体是' in prompt
    assert not re.search(r'提示词使用说明|将以上内容.*输入|提示词结束|<START>|\{\{(?:char|user)\}\}', prompt)
    assert len(re.findall(r'^## ', prompt, re.M)) >= 6
    if not config.voice_config.no_voice:
        voice = config.get_voice_config()
        for field in ('ref_audio_path', 'ref_text_path'):
            resource = Path(voice[field])
            resource.resolve().relative_to(path.parent.resolve())
            assert resource.is_file()
            content = resource.read_bytes()
            assert content.strip()
            assert not content.startswith(b'version https://git-lfs.github.com/spec/v1')
        assert config.get_ref_audio_text()


def test_previously_fixed_role_identity_does_not_regress():
    for name in ('almond_eye', 'rice_shower', 'agnes_digital', 'chrono_genesis'):
        prompt = (ROOT / name / 'prompt.md').read_text(encoding='utf-8')
        assert '不替' in prompt
        assert '你的目标是作为训练员' not in prompt
    assert '你作为**训练员**' not in (ROOT / 'still_in_love/prompt.md').read_text(encoding='utf-8')


def test_known_lore_mixups_do_not_reappear():
    bad_fragments = {
        'vivlos': ('同母异父', '父亲是传奇'),
        'fine_motion': ('繁殖母马',),
        'el_condor_pasa': ('并列日本德比冠军', '护妻狂魔'),
        'neo_universe': ('杜满莱', 'Fliament', '5月21日'),
        'still_in_love': ('幸英明', '16战'),
        'mihono_bourbon': ('左前脚籽骨骨折', '绝对服从指令'),
    }
    for name, fragments in bad_fragments.items():
        prompt = (ROOT / name / 'prompt.md').read_text(encoding='utf-8')
        for fragment in fragments:
            assert fragment not in prompt, (name, fragment)


def test_generic_anime_voice_banks_are_not_chat_characters():
    for name in ('NPC-Girl', 'NPC-Woman', 'NPC-boy', 'Trainer', 'Voice-over'):
        assert not (ROOT / name).exists(), name


def test_tazuna_is_an_explicit_staff_card_and_loads_by_all_names():
    from umamusume_agent.character.manager import CharacterManager
    import asyncio

    manager = CharacterManager(str(ROOT))
    path = ROOT / 'hayakawa_tazuna'
    for alias in ('hayakawa_tazuna', '骏川手纲', 'Tazuna Hayakawa', '駿川たづな'):
        assert manager.get_character_dir(alias) == path
    config = asyncio.run(manager.load_character('骏川手纲'))
    assert config.id == 'hayakawa_tazuna'
    assert config.get_ref_audio_text()
    assert '理事长秘书' in config.system_prompt
    assert '不是用户签约育成的学生或参赛担当' in config.system_prompt
