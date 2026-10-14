"""Update only selected fields; preserve prompt/audio edits and fail before writes."""
import importlib.util
import json
import sys
import subprocess
from pathlib import Path

import pytest

script = Path(__file__).resolve().parents[1] / "scripts" / "update_characters.py"
if not script.exists():  # permits isolated testing before installation
    script = Path(__file__).with_name("update_characters.py")
spec = importlib.util.spec_from_file_location("character_update", script)
update = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = update
spec.loader.exec_module(update)


def character(root, name="example", *, prompt="old", audio=b"audio-old"):
    directory = root / name
    directory.mkdir(parents=True)
    config = {"id": "uma_" + name, "name_en": name, "name_zh": "测试" + name,
              "system_prompt": prompt, "metadata": {"custom": "keep"}, "extension": {"x": 1},
              "voice_config": {"no_voice": "false", "ref_audio_path": "reference.wav",
                               "ref_text_path": "reference_jp.txt", "sample_rate": 48000}}
    (directory / "config.json").write_bytes(update.encoded(config))
    (directory / "prompt.md").write_text(prompt, encoding="utf-8")
    (directory / "reference.wav").write_bytes(audio)
    (directory / "reference_jp.txt").write_text("日文", encoding="utf-8")
    (directory / "reference_zh.txt").write_text("中文", encoding="utf-8")
    return directory, config


def test_prompt_sync_preserves_all_other_fields_and_is_idempotent(tmp_path):
    root = tmp_path / "characters"
    directory, before = character(root)
    (directory / "prompt.md").write_text(" 新人设\n", encoding="utf-8")
    records = update.catalog(root)
    assert update.select(records, ["测试example"], False) == ["example"]
    plan = update.plan_prompts(records, ["example"])
    assert update.read_config(directory) == before  # preview is read-only
    backup = update.apply_plan(root, plan)
    after = update.read_config(directory)
    assert after == {**before, "system_prompt": "新人设"}
    assert (backup / "before/example/config.json").exists()
    assert (directory / "reference.wav").read_bytes() == b"audio-old"
    assert update.plan_prompts(update.catalog(root), ["example"]) == []


def test_empty_prompt_aborts_whole_planning(tmp_path):
    root = tmp_path / "characters"
    first, before = character(root, "first")
    second, _ = character(root, "second")
    (first / "prompt.md").write_text("new", encoding="utf-8")
    (second / "prompt.md").write_text(" ", encoding="utf-8")
    with pytest.raises(ValueError, match="Empty prompt"):
        update.plan_prompts(update.catalog(root), ["first", "second"])
    assert update.read_config(first) == before


def test_voice_merge_keeps_target_persona_and_metadata(tmp_path):
    target, source = tmp_path / "target", tmp_path / "source"
    dest, original = character(target, prompt="agent fixed", audio=b"agent audio")
    src, config = character(source, prompt="outdated anime", audio=b"tested anime audio")
    config["voice_config"]["sample_rate"] = 44100
    (src / "config.json").write_bytes(update.encoded(config))
    (src / "reference_jp.txt").write_text("新日文", encoding="utf-8")
    (src / "reference_zh.txt").write_text("新中文", encoding="utf-8")
    (src / "unused.wav").write_bytes(b"unselected")
    plan, skipped = update.plan_voices(target, update.catalog(target), update.catalog(source), ["example"])
    update.apply_plan(target, plan)
    assert not skipped
    assert update.read_config(dest) == {**original, "voice_config": config["voice_config"]}
    assert (dest / "prompt.md").read_text() == "agent fixed"
    assert (dest / "reference.wav").read_bytes() == b"tested anime audio"
    assert (dest / "reference_jp.txt").read_text() == "新日文"
    assert (dest / "reference_zh.txt").read_text() == "新中文"
    assert not (dest / "unused.wav").exists()
    assert update.plan_voices(target, update.catalog(target), update.catalog(source), ["example"])[0] == []


def test_new_character_uses_source_md_and_copies_assets(tmp_path):
    target, source = tmp_path / "target", tmp_path / "source"
    target.mkdir()
    src, _ = character(source)
    (src / "prompt.md").write_text("latest md", encoding="utf-8")
    (src / "portrait.png").write_bytes(b"image")
    plan, _ = update.plan_voices(target, {}, update.catalog(source), ["example"], True)
    update.apply_plan(target, plan)
    assert update.read_config(target / "example")["system_prompt"] == "latest md"
    assert (target / "example/portrait.png").read_bytes() == b"image"


@pytest.mark.parametrize("bad_path", ["../outside.wav", "/tmp/outside.wav"])
def test_voice_path_escape_is_rejected(tmp_path, bad_path):
    directory, config = character(tmp_path / "characters")
    config["voice_config"]["ref_audio_path"] = bad_path
    with pytest.raises(ValueError):
        update.voice_bundle(directory, config)


def test_lfs_pointer_is_not_an_audio_reference(tmp_path):
    directory, config = character(tmp_path / "characters", audio=b"version https://git-lfs.github.com/spec/v1\n")
    with pytest.raises(ValueError, match="Git LFS"):
        update.voice_bundle(directory, config)


def test_identity_mismatch_aborts(tmp_path):
    target, source = tmp_path / "target", tmp_path / "source"
    character(target)
    src, config = character(source)
    config["id"] = "uma_someone_else"
    (src / "config.json").write_bytes(update.encoded(config))
    with pytest.raises(ValueError, match="Identity mismatch"):
        update.plan_voices(target, update.catalog(target), update.catalog(source), ["example"])


def test_concurrent_edit_is_preserved(tmp_path):
    root = tmp_path / "characters"
    directory, _ = character(root)
    (directory / "prompt.md").write_text("new", encoding="utf-8")
    plan = update.plan_prompts(update.catalog(root), ["example"])
    (directory / "config.json").write_text("user changed", encoding="utf-8")
    with pytest.raises(ValueError, match="changed after planning"):
        update.apply_plan(root, plan)
    assert (directory / "config.json").read_text() == "user changed"


def test_failure_rolls_back_completed_files(tmp_path, monkeypatch):
    root = tmp_path / "characters"
    a, ac = character(root, "a")
    b, bc = character(root, "b")
    for directory in (a, b):
        (directory / "prompt.md").write_text("new", encoding="utf-8")
    plan = update.plan_prompts(update.catalog(root), ["a", "b"])
    original_write = update.atomic_write
    def fail_second(path, content):
        if path == b / "config.json":
            raise OSError("test failure")
        original_write(path, content)
    monkeypatch.setattr(update, "atomic_write", fail_second)
    with pytest.raises(OSError, match="test failure"):
        update.apply_plan(root, plan)
    assert update.read_config(a) == ac
    assert update.read_config(b) == bc


def test_cli_preview_check_apply_and_name_selection(tmp_path):
    root = tmp_path / 'characters'
    directory, before = character(root)
    (directory / 'prompt.md').write_text('new persona', encoding='utf-8')
    command = [sys.executable, str(script), 'prompts', '--characters-dir', str(root),
               '--character', '测试example']
    preview = subprocess.run(command, capture_output=True, text=True)
    assert preview.returncode == 0, preview.stderr
    assert json.loads(preview.stdout)['applied'] is False
    assert update.read_config(directory) == before
    assert subprocess.run(command + ['--check'], capture_output=True).returncode == 1
    result = subprocess.run(command + ['--apply'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['backup']
    assert subprocess.run(command + ['--check'], capture_output=True).returncode == 0


def test_disabled_source_does_not_disable_working_target(tmp_path):
    target, source = tmp_path / 'target', tmp_path / 'source'
    dest, original = character(target)
    src, config = character(source)
    config['voice_config'] = {'no_voice': 'true'}
    (src / 'config.json').write_bytes(update.encoded(config))
    plan, skipped = update.plan_voices(target, update.catalog(target), update.catalog(source), ['example'])
    assert plan == [] and skipped
    assert update.read_config(dest) == original


def test_unsafe_symlink_is_rejected(tmp_path):
    directory, config = character(tmp_path / 'characters')
    outside = tmp_path / 'outside.wav'
    outside.write_bytes(b'audio')
    (directory / 'linked.wav').symlink_to(outside)
    config['voice_config']['ref_audio_path'] = 'linked.wav'
    with pytest.raises(ValueError):
        update.voice_bundle(directory, config)


def test_anime_audio_only_updates_existing_card_but_excludes_unconfigured_voices(tmp_path):
    target, source = tmp_path / 'target', tmp_path / 'source'
    dest, original = character(target, 'haru_urara', prompt='agent persona')
    for name in ('haru_urara', 'NPC-Girl', 'NPC-Woman', 'NPC-boy', 'Trainer', 'Voice-over', 'hayakawa_tazuna'):
        directory = source / name
        directory.mkdir(parents=True)
        (directory / 'reference.mp3').write_bytes(b'anime upgraded audio')
        (directory / 'reference_jp.txt').write_text('新音频配对文本', encoding='utf-8')
    records, excluded = update.anime_catalog(source, update.catalog(target))
    assert set(records) == {'haru_urara'}
    assert len(excluded) == 6
    plan, _ = update.plan_voices(target, update.catalog(target), records, ['haru_urara'], True, 'anime')
    update.apply_plan(target, plan)
    after = update.read_config(dest)
    assert after['system_prompt'] == original['system_prompt']
    assert after['voice_config']['ref_audio_path'] == 'reference.mp3'
    assert (dest / 'reference.mp3').read_bytes() == b'anime upgraded audio'
    assert (dest / 'reference.wav').exists()  # old assets aren't deleted
    assert set(update.catalog(target)) == {'haru_urara'}
    assert not update.plan_voices(target, update.catalog(target), records, ['haru_urara'], True, 'anime')[0]


def test_anime_uses_mp3_priority_even_when_config_selects_wav_or_disables_voice(tmp_path):
    target, source = tmp_path / 'target', tmp_path / 'source'
    dest, original = character(target)
    src, config = character(source)
    config['voice_config']['no_voice'] = True
    config['voice_config']['model'] = 'stale-model'
    (src / 'config.json').write_bytes(update.encoded(config))
    (src / 'reference.mp3').write_bytes(b'anime actual runtime choice')
    records, _ = update.anime_catalog(source, update.catalog(target))
    plan, _ = update.plan_voices(target, update.catalog(target), records, ['example'], source_layout='anime')
    update.apply_plan(target, plan)
    after = update.read_config(dest)
    assert after['voice_config']['ref_audio_path'] == 'reference.mp3'
    assert after['voice_config'].get('model') == original['voice_config'].get('model')
    assert not update.disabled(after['voice_config'])


def test_anime_does_not_guess_missing_japanese_transcript(tmp_path):
    target, source = tmp_path / 'target', tmp_path / 'source'
    character(target)
    src, _ = character(source)
    (src / 'reference_jp.txt').unlink()
    (src / 'reference.txt').write_text('unknown language', encoding='utf-8')
    with pytest.raises(OSError):
        update.plan_voices(target, update.catalog(target), update.catalog(source), ['example'], source_layout='anime')


def test_explicitly_created_staff_card_can_sync_without_importing_other_npcs(tmp_path):
    target, source = tmp_path / 'target', tmp_path / 'source'
    dest, config = character(target, 'hayakawa_tazuna', prompt='理事长秘书，不是担当选手')
    config['id'] = 'hayakawa_tazuna'
    (dest / 'config.json').write_bytes(update.encoded(config))
    source.mkdir()
    src = source / 'hayakawa_tazuna'
    src.mkdir()
    (src / 'reference.wav').write_bytes(b'staff audio')
    (src / 'reference_jp.txt').write_text('トレーナーさん', encoding='utf-8')
    records, excluded = update.anime_catalog(source, update.catalog(target))
    plan, _ = update.plan_voices(target, update.catalog(target), records, list(records), source_layout='anime')
    update.apply_plan(target, plan)
    assert not excluded
    assert update.read_config(dest)['id'] == 'hayakawa_tazuna'
    assert (dest / 'prompt.md').read_text() == '理事长秘书，不是担当选手'


def test_anime_cli_reports_excluded_audio_only_folders(tmp_path):
    target, source = tmp_path / 'target', tmp_path / 'source'
    character(target)
    character(source)
    generic = source / 'Trainer'
    generic.mkdir()
    (generic / 'reference.wav').write_bytes(b'generic trainer')
    result = subprocess.run([sys.executable, str(script), 'voices', '--characters-dir', str(target),
                             '--source', str(source), '--source-layout', 'anime', '--all', '--add-missing'],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['selected'] == 1
    assert any('Trainer' in line and 'not imported' in line for line in report['skipped'])
    assert not (target / 'Trainer').exists()
