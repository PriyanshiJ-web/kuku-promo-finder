from pathlib import Path

import pytest

from promo.parse import parse_folder

FIX = Path(__file__).parent / "fixtures" / "show_a"


@pytest.fixture(scope="session")
def show():
    return parse_folder(FIX)
