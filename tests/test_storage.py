"""Tests for project storage and persistence."""

import tempfile
from pathlib import Path

import pytest

from rea.models import (
    AssetInfo, DiscoveredAction, NetworkEvent, PageInfo,
    Project, ProjectConfig, ProjectStatus,
)
from rea.storage.database import Database
from rea.storage.projects import ProjectStore
from rea.storage.usage import UsageStore
from rea.models import UsageRecord


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "test.db")
    d.connect()
    yield d
    d.close()


@pytest.fixture
def project_store(db):
    return ProjectStore(db)


@pytest.fixture
def usage_store(db):
    return UsageStore(db)


@pytest.fixture
def sample_project():
    return Project(
        id="testproject01",
        name="Test Project",
        target_url="https://example.com",
        status=ProjectStatus.CREATED,
    )


def test_create_and_get_project(project_store, sample_project):
    project_store.create(sample_project)
    found = project_store.get(sample_project.id)
    assert found is not None
    assert found.id == sample_project.id
    assert found.name == "Test Project"
    assert found.target_url == "https://example.com"


def test_project_not_found(project_store):
    assert project_store.get("nonexistent") is None


def test_list_projects(project_store):
    for i in range(3):
        p = Project(name=f"Project {i}", target_url=f"https://example{i}.com")
        project_store.create(p)
    all_projects = project_store.list_all()
    assert len(all_projects) == 3


def test_update_status(project_store, sample_project):
    project_store.create(sample_project)
    project_store.update_status(sample_project.id, ProjectStatus.SCANNING)
    found = project_store.get(sample_project.id)
    assert found.status == ProjectStatus.SCANNING


def test_delete_project(project_store, sample_project):
    project_store.create(sample_project)
    project_store.delete(sample_project.id)
    assert project_store.get(sample_project.id) is None


def test_upsert_page(project_store, sample_project):
    project_store.create(sample_project)
    page = PageInfo(
        url="https://example.com/about",
        title="About",
        status_code=200,
        headings=["h1: About Us"],
        links=["https://example.com"],
    )
    project_store.upsert_page(sample_project.id, page)
    pages = project_store.get_pages(sample_project.id)
    assert len(pages) == 1
    assert pages[0].url == "https://example.com/about"
    assert pages[0].title == "About"


def test_upsert_page_idempotent(project_store, sample_project):
    project_store.create(sample_project)
    page = PageInfo(url="https://example.com/", title="Home")
    project_store.upsert_page(sample_project.id, page)
    page.title = "Home Updated"
    project_store.upsert_page(sample_project.id, page)
    pages = project_store.get_pages(sample_project.id)
    assert len(pages) == 1
    assert pages[0].title == "Home Updated"


def test_page_urls(project_store, sample_project):
    project_store.create(sample_project)
    for url in ["https://example.com/", "https://example.com/about"]:
        project_store.upsert_page(sample_project.id, PageInfo(url=url))
    urls = project_store.page_urls(sample_project.id)
    assert "https://example.com/" in urls
    assert "https://example.com/about" in urls


def test_save_and_get_action(project_store, sample_project):
    project_store.create(sample_project)
    action = DiscoveredAction(
        id="A001",
        page_url="https://example.com/",
        element_type="button",
        label="Submit",
    )
    project_store.save_action(sample_project.id, action)
    actions = project_store.get_actions(sample_project.id)
    assert len(actions) == 1
    assert actions[0].label == "Submit"


def test_save_network_events(project_store, sample_project):
    project_store.create(sample_project)
    events = [
        NetworkEvent(method="GET", url="https://api.example.com/v1/data", status=200, is_xhr=True),
        NetworkEvent(method="POST", url="https://api.example.com/v1/submit", status=201, is_xhr=True),
    ]
    project_store.save_network_events(sample_project.id, events)
    stored = project_store.get_network_events(sample_project.id)
    assert len(stored) == 2


def test_upsert_asset(project_store, sample_project):
    project_store.create(sample_project)
    asset = AssetInfo(
        url="https://example.com/styles.css",
        asset_type="css",
        file_size_bytes=1024,
        content_hash="abc123",
        colors=["#ff0000", "#00ff00"],
        fonts=["Inter"],
    )
    project_store.upsert_asset(sample_project.id, asset)
    assets = project_store.get_assets(sample_project.id)
    assert len(assets) == 1
    assert assets[0].asset_type == "css"
    assert "#ff0000" in assets[0].colors


def test_usage_record(usage_store, sample_project):
    record = UsageRecord(
        project_id=sample_project.id,
        provider="openai",
        model_id="gpt-4o",
        input_tokens=500,
        output_tokens=100,
    )
    usage_store.record(record)
    summary = usage_store.summary(sample_project.id)
    assert summary["total_input_tokens"] == 500
    assert summary["total_output_tokens"] == 100
    assert summary["total_api_requests"] == 1


def test_usage_cost_estimate(usage_store):
    record = UsageRecord(
        provider="openai",
        model_id="gpt-4o",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    usage_store.record(record)
    summary = usage_store.summary()
    # gpt-4o: $2.50/Mtok input + $10/Mtok output = $12.50
    assert summary["estimated_cost_usd"] == pytest.approx(12.50, rel=0.01)


def test_generated_files_manifest(project_store, sample_project):
    project_store.create(sample_project)
    project_store.record_generated_file(
        sample_project.id, "index.html", "main page", "hash001"
    )
    project_store.record_generated_file(
        sample_project.id, "styles.css", "stylesheet", "hash002"
    )
    files = project_store.get_generated_files(sample_project.id)
    assert len(files) == 2
    paths = [f["relative_path"] for f in files]
    assert "index.html" in paths
    assert "styles.css" in paths
