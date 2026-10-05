from __future__ import annotations

import json
import os

import pytest
import yaml

from ipg_pipeline import core, review_assistant
from ipg_pipeline.omegat import minimal_yaml_update


def test_native_safe_yaml_matches_python_loader_for_all_source_files():
    for path in sorted((core.ROOT / "src/ipg").rglob("*.yaml")):
        assert core.load_yaml(path) == yaml.safe_load(path.read_text(encoding="utf-8")), path


@pytest.mark.parametrize("loader", [core.SAFE_YAML_LOADER, yaml.SafeLoader])
def test_yaml_loader_remains_safe_and_reads_edits(tmp_path, monkeypatch, loader):
    monkeypatch.setattr(core, "SAFE_YAML_LOADER", loader)
    path = tmp_path / "source.yaml"
    path.write_text("zh: 初译\n", encoding="utf-8")
    assert core.load_yaml(path) == {"zh": "初译"}
    before = path.stat()
    path.write_text("zh: 修订\n", encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert core.load_yaml(path) == {"zh": "修订"}
    path.write_text("!!python/object/apply:builtins.str [unsafe]\n", encoding="utf-8")
    with pytest.raises(yaml.constructor.ConstructorError):
        core.load_yaml(path)


def test_native_minimal_writeback_preserves_unicode_offsets_and_crlf(tmp_path):
    path = tmp_path / "source.yaml"
    original = '# 注释😀\r\ntext:\r\n  en: "é English"\r\n  zh: "旧译😀" # 保留\r\n'
    path.write_bytes(original.encode("utf-8"))
    minimal_yaml_update(path, ["text", "zh"], "旧译😀", "修订中文😀\n下一行")
    expected = original.replace('"旧译😀"', json.dumps("修订中文😀\n下一行", ensure_ascii=False))
    assert path.read_bytes() == expected.encode("utf-8")
    assert core.load_yaml(path)["text"]["zh"] == "修订中文😀\n下一行"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="changed before writeback"):
        minimal_yaml_update(path, ["text", "zh"], "旧译😀", "不应写入")
    assert path.read_bytes() == before


def _schema_files(tmp_path):
    schema = tmp_path / "root.schema.json"
    dependency = tmp_path / "shared.schema.json"
    schema.write_text(json.dumps({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://example.test/root",
        "$ref": "https://example.test/shared",
    }), encoding="utf-8")
    dependency.write_text(json.dumps({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://example.test/shared",
        "enum": ["a"],
    }), encoding="utf-8")
    return schema, dependency


def test_schema_validator_reused_but_instances_are_always_validated(tmp_path):
    schema, _ = _schema_files(tmp_path)
    core._schema_validator.cache_clear()
    assert core.validate_schema("a", schema) == []
    assert core.validate_schema("b", schema)
    stats = core._schema_validator.cache_info()
    assert stats.misses == 1 and stats.hits == 1


@pytest.mark.parametrize("edited", ["root", "dependency"])
def test_schema_cache_detects_same_size_same_mtime_edits(tmp_path, edited):
    schema, dependency = _schema_files(tmp_path)
    assert core.validate_schema("a", schema) == []
    path = schema if edited == "root" else dependency
    before = path.stat()
    text = path.read_text(encoding="utf-8")
    # Padding preserves file size; dependency edits also preserve the main schema bytes.
    replacement = text.replace('"$ref": "https://example.test/shared"', '"enum": ["b"]') if edited == "root" else text.replace('["a"]', '["b"]')
    replacement += " " * (len(text) - len(replacement))
    path.write_text(replacement, encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size
    assert core.validate_schema("a", schema)
    assert core.validate_schema("b", schema) == []


@pytest.mark.parametrize("returncode", [0, 1])
def test_full_test_runner_streams_before_wait_and_keeps_failure_gate(monkeypatch, returncode):
    streamed = []

    class Process:
        stdout = iter(["collecting full tests\n", "result line\n"])

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def wait(self):
            assert streamed == ["collecting full tests\n", "result line\n"]
            return returncode

    def launch(command, **kwargs):
        assert command == [review_assistant.sys.executable, "-u", "-m", "pytest", "-o", "addopts=", "-v"]
        assert kwargs["env"]["PYTHONUTF8"] == "1"
        assert kwargs["cwd"] == review_assistant.ROOT
        assert kwargs["stderr"] == review_assistant.subprocess.STDOUT
        return Process()

    def show(line, **kwargs):
        assert kwargs == {"end": "", "flush": True}
        streamed.append(line)

    monkeypatch.setattr(review_assistant.subprocess, "Popen", launch)
    monkeypatch.setattr(review_assistant, "print", show, raising=False)
    if returncode:
        with pytest.raises(ValueError, match="完整测试失败：[\\s\\S]*result line"):
            review_assistant.run_full_tests()
    else:
        assert review_assistant.run_full_tests() == {
            "passed": True, "output": "collecting full tests\nresult line",
        }
