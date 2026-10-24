"""配置加载单测（SPEC §2）：字段锁死与非法值拒绝。"""
import pytest

from debate.core.config import AppConfig, load_config


def test_load_real_config(config):
    assert config.procedure.speed_cpm == 220
    assert config.procedure.phases.statement == 3.0
    assert config.model.temperature.debater == 0.85
    assert config.context.recent_speeches == 6
    assert config.judges.count == 3
    assert config.quota.concurrent_debates_per_user == 1
    assert "废物" in config.conduct.abuse_keywords
    assert config.conduct.max_interrupts_per_answer == 2


def test_judge_count_restricted():
    with pytest.raises(ValueError):
        AppConfig.model_validate({"judges": {"count": 2}})


def test_missing_config_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")
