import sys

import pytest

from groundtruth import handlers, telegram
from scripts import run_source, set_webhook


def test_run_source_reports_result(monkeypatch, capsys):
    monkeypatch.setattr(handlers, "source_job", lambda force=False: {"status": "skipped"})
    monkeypatch.setattr(sys, "argv", ["run_source"])
    assert run_source.main() == 0
    assert '"skipped"' in capsys.readouterr().out


def test_run_source_fails_the_execution_on_error(monkeypatch):
    """Non-zero exit marks the job execution failed, so its retry runs it again."""
    def boom(force=False):
        raise telegram.TelegramError("sendMessage failed")

    monkeypatch.setattr(handlers, "source_job", boom)
    monkeypatch.setattr(sys, "argv", ["run_source", "--force"])
    assert run_source.main() == 1


def test_set_webhook_registers_taps_only_with_secret(monkeypatch):
    calls = []
    monkeypatch.setattr(telegram, "call", lambda method, **p: calls.append((method, p)))
    set_webhook.set_webhook("https://app.example.io/")
    method, params = calls[0]
    assert method == "setWebhook"
    assert params["url"] == "https://app.example.io/telegram"
    assert params["secret_token"] == "test-webhook-secret"
    assert params["allowed_updates"] == ["callback_query"]


def test_set_webhook_refuses_without_secret(monkeypatch):
    monkeypatch.setattr(set_webhook, "TELEGRAM_WEBHOOK_SECRET", None)
    with pytest.raises(SystemExit):
        set_webhook.set_webhook("https://app.example.io")
