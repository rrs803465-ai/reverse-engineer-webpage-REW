"""Core data models for REW.

Uses stdlib dataclasses so the application runs without Pydantic installed.
Pydantic remains in requirements.txt for users who want full validation;
this file is the runtime-safe fallback.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ProviderType(str, Enum):
    OPENROUTER = "openrouter"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    COMPATIBLE = "compatible"


class ScanDepth(str, Enum):
    SHALLOW = "shallow"
    STANDARD = "standard"
    DEEP = "deep"
    CUSTOM = "custom"


class ActionConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class ProjectStatus(str, Enum):
    CREATED = "created"
    SCANNING = "scanning"
    ANALYZING = "analyzing"
    GENERATING = "generating"
    TESTING = "testing"
    COMPLETE = "complete"
    PAUSED = "paused"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Provider & model metadata
# ---------------------------------------------------------------------------

@dataclass
class ModelInfo:
    id: str
    name: str | None = None
    provider: str | None = None
    context_window: int | None = None
    input_price_per_mtok: float | None = None
    output_price_per_mtok: float | None = None
    supports_tools: bool | None = None
    supports_vision: bool | None = None
    supports_structured_output: bool | None = None
    supports_streaming: bool | None = None
    max_output_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def display_name(self) -> str:
        return self.name or self.id


@dataclass
class ProviderConfig:
    type: ProviderType
    name: str
    base_url: str | None = None
    model_id: str | None = None
    fallback_model_id: str | None = None
    timeout_seconds: int = 60
    max_retries: int = 5
    extra_params: dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "name": self.name,
            "base_url": self.base_url,
            "model_id": self.model_id,
            "fallback_model_id": self.fallback_model_id,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "extra_params": self.extra_params,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProviderConfig":
        d = dict(d)
        d["type"] = ProviderType(d["type"])
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Scan & analysis results
# ---------------------------------------------------------------------------

@dataclass
class PageInfo:
    url: str
    canonical_url: str | None = None
    title: str | None = None
    status_code: int | None = None
    content_type: str | None = None
    headings: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    forms: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, str] = field(default_factory=dict)
    screenshot_path: str | None = None
    scanned_at: datetime = field(default_factory=datetime.utcnow)
    console_errors: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)


@dataclass
class DiscoveredAction:
    page_url: str
    element_type: str
    id: str = field(default_factory=lambda: f"A{uuid.uuid4().hex[:6].upper()}")
    label: str | None = None
    selector: str | None = None
    aria_label: str | None = None
    initial_state: str | None = None
    observed_result: str | None = None
    url_changed: bool = False
    network_activity: bool = False
    confidence: ActionConfidence = ActionConfidence.UNKNOWN
    notes: str | None = None


@dataclass
class NetworkEvent:
    method: str
    url: str
    resource_type: str | None = None
    status: int | None = None
    content_type: str | None = None
    duration_ms: float | None = None
    is_xhr: bool = False
    is_websocket: bool = False
    request_body_summary: str | None = None
    response_body_summary: str | None = None
    associated_action_id: str | None = None


@dataclass
class AssetInfo:
    url: str
    asset_type: str
    file_size_bytes: int | None = None
    content_hash: str | None = None
    local_path: str | None = None
    extracted_vars: dict[str, str] = field(default_factory=dict)
    fonts: list[str] = field(default_factory=list)
    colors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

@dataclass
class ProjectConfig:
    scan_depth: ScanDepth = ScanDepth.STANDARD
    max_pages: int = 20
    max_tool_calls: int = 100
    max_iterations: int = 50
    input_token_budget: int = 500_000
    output_token_budget: int = 100_000
    cost_budget_usd: float | None = None
    headless: bool = True
    viewport_width: int = 1280
    viewport_height: int = 800
    request_timeout_seconds: int = 30
    allowed_domains: list[str] = field(default_factory=list)
    capture_screenshots: bool = True
    full_page_screenshots: bool = False

    def model_dump_json(self) -> str:
        d = {
            "scan_depth": self.scan_depth.value,
            "max_pages": self.max_pages,
            "max_tool_calls": self.max_tool_calls,
            "max_iterations": self.max_iterations,
            "input_token_budget": self.input_token_budget,
            "output_token_budget": self.output_token_budget,
            "cost_budget_usd": self.cost_budget_usd,
            "headless": self.headless,
            "viewport_width": self.viewport_width,
            "viewport_height": self.viewport_height,
            "request_timeout_seconds": self.request_timeout_seconds,
            "allowed_domains": self.allowed_domains,
            "capture_screenshots": self.capture_screenshots,
            "full_page_screenshots": self.full_page_screenshots,
        }
        return json.dumps(d)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProjectConfig":
        if not d:
            return cls()
        d = dict(d)
        if "scan_depth" in d:
            d["scan_depth"] = ScanDepth(d["scan_depth"])
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


@dataclass
class Project:
    name: str
    target_url: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: ProjectStatus = ProjectStatus.CREATED
    provider_name: str | None = None
    model_id: str | None = None
    config: ProjectConfig = field(default_factory=ProjectConfig)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    workspace_path: str | None = None
    notes: str | None = None


# ---------------------------------------------------------------------------
# Token / cost accounting
# ---------------------------------------------------------------------------

@dataclass
class UsageRecord:
    provider: str
    model_id: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    project_id: str | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    api_requests: int = 1
    retried_requests: int = 0
    estimated_cost_usd: float | None = None
    source: str = "api"
    task_label: str | None = None


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@dataclass
class ToolParameter:
    name: str
    type: str
    description: str
    required: bool = True
    enum: list[str] | None = None


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: list[ToolParameter] = field(default_factory=list)

    def to_openai_schema(self) -> dict[str, Any]:
        props: dict[str, Any] = {}
        required: list[str] = []
        for p in self.parameters:
            prop: dict[str, Any] = {"type": p.type, "description": p.description}
            if p.enum:
                prop["enum"] = p.enum
            props[p.name] = prop
            if p.required:
                required.append(p.name)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        }

    def to_anthropic_schema(self) -> dict[str, Any]:
        props: dict[str, Any] = {}
        required: list[str] = []
        for p in self.parameters:
            prop: dict[str, Any] = {"type": p.type, "description": p.description}
            if p.enum:
                prop["enum"] = p.enum
            props[p.name] = prop
            if p.required:
                required.append(p.name)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        }
