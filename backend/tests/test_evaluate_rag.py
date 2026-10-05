import importlib.util
import sys
from pathlib import Path

from sqlalchemy.exc import OperationalError


def _evaluate_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "evaluate_rag.py"
    spec = importlib.util.spec_from_file_location("qinghe_evaluate_rag_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_mysql_connection_failure_is_short_and_never_resets_data(monkeypatch, tmp_path, capsys):
    module = _evaluate_module()

    class FailingSession:
        def __enter__(self):
            raise OperationalError("SELECT 1", {}, OSError("connection refused"))

        def __exit__(self, *_args):
            return False

    output = tmp_path / "evaluation.json"
    monkeypatch.setattr(module, "SessionLocal", lambda: FailingSession())
    monkeypatch.setattr(sys, "argv", ["evaluate_rag.py", "--output", str(output)])

    assert module.main() == 2
    error = capsys.readouterr().err
    assert "项目专用 MySQL（13308）" in error
    assert "mysql_runtime.py start" in error
    assert "没有重置任何数据" in error
    assert "Traceback" not in error
    assert not output.exists()
