"""The worker's container-version gate compares `gttp --version` with
gttp.__version__, so the package version must match pyproject.toml."""

import tomllib

from gttp import __version__
from gttp.config import ROOT


def test_version_matches_pyproject():
    with open(ROOT / "pyproject.toml", "rb") as f:
        assert tomllib.load(f)["project"]["version"] == __version__
