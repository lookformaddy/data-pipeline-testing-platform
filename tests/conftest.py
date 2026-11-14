"""Pytest configuration: put the src layout on the import path so plain
``pytest`` passes from the project root, with or without an install.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def engine(tmp_path):
    from pipetest.engine import TestEngine

    return TestEngine(base_dir=tmp_path)


@pytest.fixture()
def clean_tables():
    from pipetest.seed import BASE_TIME, seed_tables

    return seed_tables(now=BASE_TIME)


@pytest.fixture()
def suite():
    from pipetest.demo import orders_suite

    return orders_suite()
