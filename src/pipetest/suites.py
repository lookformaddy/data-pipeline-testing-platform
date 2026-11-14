"""Versioned test suites with promotion.

Suites live as JSON files under ``<base_dir>/suites/<name>/<version>.json``.
A version is immutable once written — fixing a suite means shipping a new
version, so a run's ``suite_version`` always names exactly the tests that
ran. Promotion moves a version forward through
draft → staging → production only; demotion and skipping stages are
rejected, so a suite cannot reach production without passing staging.
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import Stage, TestSuite

_STAGE_ORDER = [Stage.DRAFT, Stage.STAGING, Stage.PRODUCTION]


class SuiteRegistry:
    def __init__(self, base_dir: Path | str) -> None:
        self.base_dir = Path(base_dir)
        self.suites_dir = self.base_dir / "suites"
        self.suites_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str, version: str) -> Path:
        return self.suites_dir / name / f"{version}.json"

    def save(self, suite: TestSuite) -> Path:
        """Write a new suite version. Raises if the version already exists."""
        path = self._path(suite.name, suite.version)
        if path.exists():
            raise ValueError(
                f"suite {suite.name} version {suite.version} already exists — "
                "versions are immutable; ship a new version instead"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(suite.to_dict(), indent=2, sort_keys=True))
        return path

    def load(self, name: str, version: str) -> TestSuite:
        path = self._path(name, version)
        if not path.exists():
            raise KeyError(f"no suite {name!r} version {version!r}")
        return TestSuite.from_dict(json.loads(path.read_text()))

    def versions(self, name: str) -> list[TestSuite]:
        directory = self.suites_dir / name
        if not directory.exists():
            return []
        suites = [TestSuite.from_dict(json.loads(p.read_text()))
                  for p in sorted(directory.glob("*.json"))]
        return sorted(suites, key=lambda s: s.version)

    def latest(self, name: str, stage: Stage | None = None) -> TestSuite:
        suites = self.versions(name)
        if stage is not None:
            suites = [s for s in suites if s.stage == stage]
        if not suites:
            raise KeyError(f"no suite {name!r}" + (f" in stage {stage.value!r}" if stage else ""))
        return suites[-1]

    def promote(self, name: str, version: str, to_stage: Stage) -> TestSuite:
        """Promote a version one stage forward. Returns the updated suite."""
        suite = self.load(name, version)
        current = _STAGE_ORDER.index(suite.stage)
        target = _STAGE_ORDER.index(to_stage)
        if target != current + 1:
            raise ValueError(
                f"cannot promote {name} v{version} from {suite.stage.value} "
                f"to {to_stage.value}: promotion moves exactly one stage forward"
            )
        suite.stage = to_stage
        path = self._path(name, version)
        path.write_text(json.dumps(suite.to_dict(), indent=2, sort_keys=True))
        return suite

    def list_names(self) -> list[str]:
        if not self.suites_dir.exists():
            return []
        return sorted(p.name for p in self.suites_dir.iterdir() if p.is_dir())
