from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

import generate_samples
from app.config import Settings


@pytest.fixture(scope="session")
def samples(tmp_path_factory) -> dict[str, Path]:
    """Generate every sample PDF once per test session."""
    directory = tmp_path_factory.mktemp("samples")
    paths = {}
    for name, factory in generate_samples.SAMPLES.items():
        path = directory / name
        path.write_bytes(factory())
        paths[name.removesuffix(".pdf")] = path
    return paths


@pytest.fixture
def settings(tmp_path) -> Settings:
    return dataclasses.replace(Settings(), work_dir=tmp_path / "work")


@pytest.fixture
def out(tmp_path) -> Path:
    return tmp_path / "out.docx"
