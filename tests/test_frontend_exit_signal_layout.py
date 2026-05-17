from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_INDEX = ROOT / "frontend" / "index.html"
FRONTEND_APP = ROOT / "frontend" / "js" / "app.js"


def test_positions_page_includes_exit_signal_and_continue_modals():
    html = FRONTEND_INDEX.read_text()

    assert 'id="exit-signal-modal"' in html
    assert "持仓动作建议" in html
    assert 'id="continue-hold-modal"' in html
    assert 'id="close-position-exit-signal-id"' in html


def test_positions_js_wires_exit_signal_actions_and_close_reasons():
    js = FRONTEND_APP.read_text()

    assert "renderExitSignalStrip" in js
    assert "openExitSignalModal" in js
    assert "openContinueHoldModal" in js
    assert "action-log" in js
    assert "take_profit_fast" in js
    assert "loss_breach" in js
    assert "/api/positions/marks?fast=1" in js
    assert "urgencyTooltipHtml" in js
    assert "不是收益预测" in js


def test_urgency_tooltip_keeps_only_custom_layer():
    js = FRONTEND_APP.read_text()

    match = re.search(r"function urgencyTooltipHtml\(\) \{(?P<body>.*?)\n\}", js, re.S)
    assert match is not None
    body = match.group("body")
    assert "role=\"tooltip\"" in body
    assert "title=" not in body


def test_exit_signal_modal_uses_chinese_labels_and_threshold_scenarios():
    js = FRONTEND_APP.read_text()
    reasons_match = re.search(
        r"function renderExitReasons\(signal\) \{(?P<body>.*?)\n\}\n\nwindow.openExitSignalModal",
        js,
        re.S,
    )
    assert reasons_match is not None
    reasons_body = reasons_match.group("body")

    assert "exitSeverityLabel" in js
    assert "renderExitScenarioTable" in js
    assert "退出阈值场景" in js
    assert "估算收益" in js
    assert "severity ${" not in js
    assert "JSON.stringify(reason.current" not in reasons_body
    assert "JSON.stringify(reason.threshold" not in reasons_body
