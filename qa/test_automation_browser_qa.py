from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class BrowserQAServer(ThreadingHTTPServer):
    """Test-only HTTP server whose request threads cannot block teardown."""

    daemon_threads = True
    block_on_close = False
from pathlib import Path
from urllib.parse import unquote, urlparse

from playwright.sync_api import sync_playwright, expect

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from wechat_console.tests import mock_core
from wechat_console.tests.test_agent_automation_proxy import MockAgentHandler, MockAgentState
from wechat_console.app import ConsoleService, create_server


def test_automation_browser_smoke():
    """Gate P2-7: Automation Console Browser Smoke Test using Playwright."""
    js_errors = []

    # 1. Setup Mock Core with identity scenario (alpha bound, gamma mismatch)
    core_state = mock_core.MockCoreState(identity_scenario=True)
    mock_core_server = mock_core.create_server("127.0.0.1", 0, core_state)
    core_url = f"http://127.0.0.1:{mock_core_server.server_port}"
    mock_core_thread = threading.Thread(target=mock_core_server.serve_forever, daemon=True)
    mock_core_thread.start()

    # 2. Setup Mock Agent
    agent_state = MockAgentState()
    # Seed initial items
    agent_state.monitors["mon-seed"] = {
        "monitor_id": "mon-seed",
        "name": "种子规则",
        "account_id": "account-alpha",
        "chat_id": "",
        "action": "send_text",
        "contains_text": "种子",
        "action_config": {"text": "种子回复"},
        "enabled": True,
        "expected_wechat_identity_uuid": mock_core.IDENTITY_ALPHA_UUID,
    }
    agent_state.runs["mon-seed"] = [
        {
            "run_id": "run-001",
            "monitor_id": "mon-seed",
            "status": "success",
            "error_code": "",
            "identity_uuid": mock_core.IDENTITY_ALPHA_UUID,
            "instance_uuid": mock_core.INSTANCE_ALPHA_UUID,
            "executed_at": "2026-09-06T12:00:00Z",
            "details": {"action": "send_text", "sent": True},
        }
    ]
    agent_state.schedules["sched-seed"] = {
        "schedule_id": "sched-seed",
        "name": "种子定时",
        "account_id": "account-alpha",
        "chat_id": "alpha-private-1",
        "task_type": "send_text",
        "interval_seconds": 3600,
        "payload": {"text": "定时内容"},
        "enabled": True,
        "instance_uuid": mock_core.INSTANCE_ALPHA_UUID,
        "expected_wechat_identity_uuid": mock_core.IDENTITY_ALPHA_UUID,
    }
    agent_state.templates["tmpl-seed"] = {
        "template_id": "tmpl-seed",
        "name": "种子模板",
        "body": "您好，这是种子模板",
        "variables": [],
        "enabled": True,
    }

    handler_cls = type("BoundMockAgentHandler", (MockAgentHandler,), {"state": agent_state})
    mock_agent_server = BrowserQAServer(("127.0.0.1", 0), handler_cls)
    agent_url = f"http://127.0.0.1:{mock_agent_server.server_port}"
    mock_agent_thread = threading.Thread(target=mock_agent_server.serve_forever, daemon=True)
    mock_agent_thread.start()

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Service A: Agent unconfigured
        service_unconf = ConsoleService(
            core_url=core_url,
            db_path=tmp_path / "console_unconf.sqlite",
            archive_dir=tmp_path / "arch_unconf",
            agent_url="",
        )
        server_unconf = create_server("127.0.0.1", 0, service_unconf)
        t_unconf = threading.Thread(target=server_unconf.serve_forever, daemon=True)
        t_unconf.start()
        base_unconf = f"http://127.0.0.1:{server_unconf.server_port}"

        # Service B: Agent configured
        service_conf = ConsoleService(
            core_url=core_url,
            db_path=tmp_path / "console_conf.sqlite",
            archive_dir=tmp_path / "arch_conf",
            agent_url=agent_url,
        )
        server_conf = create_server("127.0.0.1", 0, service_conf)
        t_conf = threading.Thread(target=server_conf.serve_forever, daemon=True)
        t_conf.start()
        base_conf = f"http://127.0.0.1:{server_conf.server_port}"

        try:
            print("[P2-7] starting Playwright", flush=True)
            with sync_playwright() as p:
                print("[P2-7] launching Edge", flush=True)
                browser = p.chromium.launch(channel="msedge", headless=True)
                print("[P2-7] creating page", flush=True)
                page = browser.new_page()
                page.set_default_timeout(10000)
                page.on("pageerror", lambda err: js_errors.append(str(err)))
                print("[P2-7] browser ready", flush=True)

                # -------------------------------------------------------------
                # Item 1: Unconfigured Agent state
                # -------------------------------------------------------------
                page.goto(f"{base_unconf}/#/automation")
                page.wait_for_selector("#automationBody")
                page.wait_for_timeout(500)
                body_text = page.inner_text("#automationBody")
                assert "未启用自动化服务" in body_text, f"Expected unconfigured message, got: {body_text}"
                assert "当前版本尚未提供此自动化能力" in body_text
                # Assert no fake rules rendered
                assert page.query_selector("#monitorRows") is None

                # -------------------------------------------------------------
                # Item 12: Check API methods are defined in JS
                # -------------------------------------------------------------
                api_methods_check = page.evaluate("""() => {
                    return import('./js/api.js').then(m => {
                        const api = m.api;
                        return {
                            hasAgentStatus: typeof api.agentStatus === 'function',
                            hasAgentMonitors: typeof api.agentMonitors === 'function',
                            hasSaveAgentMonitor: typeof api.saveAgentMonitor === 'function',
                            hasDeleteAgentMonitor: typeof api.deleteAgentMonitor === 'function',
                            hasAgentMonitorRuns: typeof api.agentMonitorRuns === 'function',
                            hasAgentSchedules: typeof api.agentSchedules === 'function',
                            hasSaveAgentSchedule: typeof api.saveAgentSchedule === 'function',
                            hasDeleteAgentSchedule: typeof api.deleteAgentSchedule === 'function',
                            hasAgentScheduleRuns: typeof api.agentScheduleRuns === 'function',
                            hasAgentTemplates: typeof api.agentTemplates === 'function',
                            hasSaveAgentTemplate: typeof api.saveAgentTemplate === 'function',
                            hasDeleteAgentTemplate: typeof api.deleteAgentTemplate === 'function',
                        };
                    });
                }""")
                for k, v in api_methods_check.items():
                    assert v is True, f"API method check failed for {k}: {v}"

                # -------------------------------------------------------------
                # Item 2: Configured Agent -> Real lists visible
                # -------------------------------------------------------------
                page.goto(f"{base_conf}/#/automation")
                page.wait_for_selector("#monitorRows")
                page.wait_for_timeout(500)

                assert "种子规则" in page.inner_text("#monitorRows")
                assert "种子定时" in page.inner_text("#scheduleRows")
                assert "种子模板" in page.inner_text("#templateRows")

                # -------------------------------------------------------------
                # Item 3: Create new monitor
                # -------------------------------------------------------------
                page.click("#newMonitorBtn")
                page.wait_for_selector("#monitorDialog[open]", state="visible")
                page.fill("#monitorName", "自动化问答")
                page.select_option("#monitorAccount", "account-alpha")
                page.select_option("#monitorAction", "send_text")
                page.fill("#monitorKeyword", "价格")
                page.fill("#monitorText", "请咨询销售")
                page.click("#monitorSaveBtn")
                page.wait_for_selector("#monitorDialog[open]", state="hidden")
                page.wait_for_timeout(500)
                assert "自动化问答" in page.inner_text("#monitorRows")

                # -------------------------------------------------------------
                # Item 4: Edit monitor
                # -------------------------------------------------------------
                rows = page.query_selector_all("#monitorRows .row")
                target_row = None
                for r in rows:
                    if "自动化问答" in r.inner_text():
                        target_row = r
                        break
                assert target_row is not None
                target_row.query_selector('[data-act="edit"]').click()
                page.wait_for_selector("#monitorDialog[open]", state="visible")
                page.fill("#monitorText", "最新价格已更新")
                page.click("#monitorSaveBtn")
                page.wait_for_selector("#monitorDialog[open]", state="hidden")
                page.wait_for_timeout(500)

                # -------------------------------------------------------------
                # Item 5: Enable / disable toggle
                # -------------------------------------------------------------
                target_row = [r for r in page.query_selector_all("#monitorRows .row") if "自动化问答" in r.inner_text()][0]
                toggle_btn = target_row.query_selector('[data-act="toggle"]')
                orig_label = toggle_btn.inner_text().strip()
                toggle_btn.click()
                page.wait_for_timeout(500)
                new_row = [r for r in page.query_selector_all("#monitorRows .row") if "自动化问答" in r.inner_text()][0]
                new_label = new_row.query_selector('[data-act="toggle"]').inner_text().strip()
                assert orig_label != new_label

                # -------------------------------------------------------------
                # Item 6: Delete monitor
                # -------------------------------------------------------------
                target_row = [r for r in page.query_selector_all("#monitorRows .row") if "自动化问答" in r.inner_text()][0]
                target_row.query_selector('[data-act="delete"]').click()
                page.wait_for_selector("#confirmDialog[open]", state="visible")
                page.click("#confirmOkBtn")
                page.wait_for_timeout(500)
                assert "自动化问答" not in page.inner_text("#monitorRows")

                # -------------------------------------------------------------
                # Item 7: View runs dialog
                # -------------------------------------------------------------
                seed_row = [r for r in page.query_selector_all("#monitorRows .row") if "种子规则" in r.inner_text()][0]
                seed_row.query_selector('[data-act="runs"]').click()
                page.wait_for_selector("#runsDialog[open]", state="visible")
                page.wait_for_selector("#runsBody .row", state="visible")
                runs_body = page.inner_text("#runsBody")
                assert "成功" in runs_body or "success" in runs_body or "12:00:00" in runs_body
                page.click("#runsCloseBtn")
                page.wait_for_selector("#runsDialog[open]", state="hidden")

                # -------------------------------------------------------------
                # Item 8: Schedule create / edit / delete
                # -------------------------------------------------------------
                page.click("#newScheduleBtn")
                page.wait_for_selector("#scheduleDialog[open]", state="visible")
                page.fill("#scheduleName", "每日晨报")
                page.select_option("#scheduleAccount", "account-alpha")
                page.select_option("#scheduleTaskType", "send_text")
                page.fill("#scheduleChat", "alpha-private-1")
                page.fill("#scheduleText", "早安！今日计划如下…")
                page.click("#scheduleSaveBtn")
                page.wait_for_selector("#scheduleDialog[open]", state="hidden")
                page.wait_for_timeout(500)
                assert "每日晨报" in page.inner_text("#scheduleRows")

                sched_row = [r for r in page.query_selector_all("#scheduleRows .row") if "每日晨报" in r.inner_text()][0]
                sched_row.query_selector('[data-act="edit"]').click()
                page.wait_for_selector("#scheduleDialog[open]", state="visible")
                assert page.input_value("#scheduleName") == "每日晨报"
                assert page.input_value("#scheduleChat") == "alpha-private-1"
                page.fill("#scheduleName", "每日晨报已更新")
                page.fill("#scheduleText", "早安！更新后的今日计划")
                page.click("#scheduleSaveBtn")
                page.wait_for_selector("#scheduleDialog[open]", state="hidden")
                page.wait_for_timeout(500)
                assert "每日晨报已更新" in page.inner_text("#scheduleRows")
                assert any(schedule.get("name") == "每日晨报已更新" and schedule.get("payload", {}).get("text") == "早安！更新后的今日计划" for schedule in agent_state.schedules.values())

                sched_row = [r for r in page.query_selector_all("#scheduleRows .row") if "每日晨报已更新" in r.inner_text()][0]
                sched_row.query_selector('[data-act="delete"]').click()
                page.wait_for_selector("#confirmDialog[open]", state="visible")
                page.click("#confirmOkBtn")
                page.wait_for_timeout(500)
                assert "每日晨报" not in page.inner_text("#scheduleRows")

                # -------------------------------------------------------------
                # Item 9: Template create / edit / delete
                # -------------------------------------------------------------
                page.click("#newTemplateBtn")
                page.wait_for_selector("#templateDialog[open]", state="visible")
                page.fill("#templateName", "欢迎模板")
                page.fill("#templateBody", "欢迎来到团队！")
                page.click("#templateSaveBtn")
                page.wait_for_selector("#templateDialog[open]", state="hidden")
                page.wait_for_timeout(500)
                assert "欢迎模板" in page.inner_text("#templateRows")

                tmpl_row = [r for r in page.query_selector_all("#templateRows .row") if "欢迎模板" in r.inner_text()][0]
                tmpl_row.query_selector('[data-act="edit"]').click()
                page.wait_for_selector("#templateDialog[open]", state="visible")
                assert page.input_value("#templateName") == "欢迎模板"
                assert page.input_value("#templateBody") == "欢迎来到团队！"
                page.fill("#templateName", "欢迎模板已更新")
                page.fill("#templateBody", "欢迎加入更新后的团队！")
                page.click("#templateSaveBtn")
                page.wait_for_selector("#templateDialog[open]", state="hidden")
                page.wait_for_timeout(500)
                assert "欢迎模板已更新" in page.inner_text("#templateRows")
                assert any(template.get("name") == "欢迎模板已更新" and template.get("body") == "欢迎加入更新后的团队！" for template in agent_state.templates.values())

                tmpl_row = [r for r in page.query_selector_all("#templateRows .row") if "欢迎模板已更新" in r.inner_text()][0]
                tmpl_row.query_selector('[data-act="delete"]').click()
                page.wait_for_selector("#confirmDialog[open]", state="visible")
                page.click("#confirmOkBtn")
                page.wait_for_timeout(500)
                assert "欢迎模板" not in page.inner_text("#templateRows")

                # -------------------------------------------------------------
                # Item 10: unbound/mismatch account cannot create send_text
                # -------------------------------------------------------------
                page.click("#newMonitorBtn")
                page.wait_for_selector("#monitorDialog[open]", state="visible")
                page.fill("#monitorName", "非法绑定规则")
                page.select_option("#monitorAccount", "account-gamma")  # gamma is mismatch
                page.select_option("#monitorAction", "send_text")
                page.fill("#monitorKeyword", "test")
                page.fill("#monitorText", "reply")
                page.click("#monitorSaveBtn")
                page.wait_for_timeout(500)
                # Modal should stay open or show field error, not close
                dialog_open = page.is_visible("#monitorDialog[open]")
                assert dialog_open is True, "Modal should stay open when unbound/mismatch account tries send_text"
                err_text = page.inner_text("#monitorDialog")
                assert "尚未绑定身份" in err_text or "拒绝" in err_text
                page.click("#monitorCancelBtn")
                page.wait_for_selector("#monitorDialog[open]", state="hidden")

                # -------------------------------------------------------------
                # Item 11: Zero unhandled JS errors
                # -------------------------------------------------------------
                assert len(js_errors) == 0, f"Collected JS errors: {js_errors}"

                browser.close()
        finally:
            server_unconf.shutdown()
            server_unconf.server_close()
            t_unconf.join(timeout=2)
            server_conf.shutdown()
            server_conf.server_close()
            t_conf.join(timeout=2)
            mock_core_server.shutdown()
            mock_core_server.server_close()
            mock_core_thread.join(timeout=2)
            mock_agent_server.shutdown()
            mock_agent_server.server_close()
            mock_agent_thread.join(timeout=2)

    print("PASS: Gate P2-7 Automation Browser Smoke completed successfully!")
