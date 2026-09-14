import subprocess
import sys

from hireshire.webapp.runner import _build_child_env


def test_child_rich_logging_uses_utf8_for_redirected_output(tmp_path):
    log_path = tmp_path / "child.log"
    script = (
        "import logging\n"
        "from rich.console import Console\n"
        "from rich.logging import RichHandler\n"
        "logging.basicConfig(handlers=[RichHandler(console=Console())], level=logging.INFO)\n"
        "logging.getLogger(__name__).info('Unicode: − → 😀')\n"
    )

    with log_path.open("w", encoding="utf-8") as log_file:
        completed = subprocess.run(
            [sys.executable, "-c", script],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=_build_child_env(),
            check=False,
        )

    output = log_path.read_text(encoding="utf-8")
    assert completed.returncode == 0
    assert "Unicode: − → 😀" in output
    assert "UnicodeEncodeError" not in output
