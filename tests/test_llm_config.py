import pytest

from llm.config import ModelConfigError, ModelsConfig, RoleNotFoundError


def test_load_computes_stable_config_hash():
    cfg1 = ModelsConfig.load()
    cfg2 = ModelsConfig.load()
    assert cfg1.config_hash == cfg2.config_hash
    assert len(cfg1.config_hash) == 16


def test_role_resolves_model_id_from_registry():
    cfg = ModelsConfig.load()
    role = cfg.role("synthesis_daily")
    assert role.model == "deepseek"
    assert role.model_id == cfg.raw["models"]["deepseek"]["id"]
    assert role.fallback == ["sonnet", "gpt", "grok", "haiku"]


def test_role_raises_for_unknown_role():
    cfg = ModelsConfig.load()
    with pytest.raises(RoleNotFoundError):
        cfg.role("does_not_exist")


def test_load_rejects_require_roles_not_declared(tmp_path):
    bad = tmp_path / "models.yaml"
    bad.write_text(
        "version: 1\nprovider: anthropic\n"
        "models:\n  sonnet: {id: x, usd_per_mtok: {in: 1, out: 1}}\n"
        "roles:\n  foo: {model: sonnet}\n"
        "validation:\n  require_roles: [bar]\n"
    )
    with pytest.raises(ModelConfigError):
        ModelsConfig.load(bad)
