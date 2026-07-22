from pathlib import Path

from subtitle_translator.cli import main

FIX = Path(__file__).parent / "fixtures"


def test_cli_mock_provider_writes_srt(tmp_path, capsys):
    out = tmp_path / "out.srt"
    code = main([str(FIX / "sample.srt"), "--provider", "mock", "-o", str(out)])
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "-->" in text
    err = capsys.readouterr().err
    assert "[stats]" in err


def test_cli_reports_load_error(tmp_path, capsys):
    missing = tmp_path / "nope.srt"
    code = main([str(missing), "--provider", "mock"])
    assert code == 2
    assert "could not load input" in capsys.readouterr().err
