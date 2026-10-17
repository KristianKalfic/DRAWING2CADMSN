import shutil

import pytest
from conftest import BCT_DATA


def test_imports():
    import cadquery
    from mcp.server.mcpserver import Image, MCPServer

    import d2c  # resolves through the editable install, also with the repo mounted over /app

    assert cadquery.__version__ == "2.8.0"


def test_pdftoppm_available():
    assert shutil.which("pdftoppm") is not None
    assert shutil.which("pdftotext") is not None


@pytest.mark.bct
def test_bct_marker_skips_without_data():
    assert BCT_DATA.is_dir()
