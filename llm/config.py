"""Loads config/models.yaml and computes config_hash (spec §5.7.4)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

MODELS_PATH = Path(__file__).parent.parent / "config" / "models.yaml"

# Params the Claude 5.x line rejects outright (spec §5.7.8/§5.7.4
# validation.forbid_params) — checked at startup, not at call time.
_FORBIDDEN_CALL_PARAMS = {"temperature", "top_p", "top_k", "assistant_prefill", "thinking_budget", "forced_tool_choice"}


class RoleNotFoundError(Exception):
    pass


class ModelConfigError(Exception):
    """Raised when models.yaml is missing a role required_roles depends on,
    or declares a forbidden call param — fail closed at load time (spec §5.7.9)."""


@dataclass
class RoleConfig:
    name: str
    model: str
    model_id: str
    provider: str
    effort: str
    mode: str
    output_schema: str | None
    prompt_version: str | None
    tools: list
    fallback: list[str]


@dataclass
class ModelsConfig:
    raw: dict
    config_hash: str

    @classmethod
    def load(cls, path: Path = MODELS_PATH) -> "ModelsConfig":
        raw = yaml.safe_load(path.read_text())
        validation = raw.get("validation", {})
        for required in validation.get("require_roles", []):
            if required not in raw["roles"]:
                raise ModelConfigError(f"models.yaml validation.require_roles names {required!r}, not declared in roles")
        forbidden_declared = set(validation.get("forbid_params", []))
        unknown_forbidden = forbidden_declared - _FORBIDDEN_CALL_PARAMS
        if unknown_forbidden:
            raise ModelConfigError(f"validation.forbid_params lists unrecognized params: {unknown_forbidden}")
        config_hash = hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()[:16]
        return cls(raw=raw, config_hash=config_hash)

    def role(self, name: str) -> RoleConfig:
        role_raw = self.raw["roles"].get(name)
        if role_raw is None:
            raise RoleNotFoundError(f"role {name!r} not declared in models.yaml")
        model_key = role_raw["model"]
        model_entry = self.raw["models"][model_key]
        return RoleConfig(
            name=name,
            model=model_key,
            model_id=model_entry["id"],
            provider=model_entry.get("provider", "anthropic"),
            effort=role_raw.get("effort", "medium"),
            mode=role_raw.get("mode", "sync"),
            output_schema=role_raw.get("output_schema"),
            prompt_version=role_raw.get("prompt_version"),
            tools=role_raw.get("tools", []),
            fallback=role_raw.get("fallback", []),
        )

    def model_provider(self, model_key: str) -> str:
        return self.raw["models"][model_key].get("provider", "anthropic")

    def model_id(self, model_key: str) -> str:
        return self.raw["models"][model_key]["id"]

    def price(self, model_key: str) -> dict:
        return self.raw["models"][model_key]["usd_per_mtok"]

    def limits(self) -> dict:
        return self.raw.get("limits", {})
