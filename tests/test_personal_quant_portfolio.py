# -*- coding: utf-8 -*-
"""Tests for personal quant account metadata and dashboard overlays."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
try:
    from fastapi.testclient import TestClient
except ModuleNotFoundError:
    TestClient = None

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import src.auth as auth
from src.config import Config
from src.services.personal_quant_service import PersonalQuantService
from src.services.portfolio_risk_service import PortfolioRiskService
from src.services.portfolio_service import PortfolioService
from src.storage import DatabaseManager


def _reset_auth_globals() -> None:
    auth._auth_enabled = None
    auth._session_secret = None
    auth._password_hash_salt = None
    auth._password_hash_stored = None
    auth._rate_limit = {}


class PersonalQuantPortfolioTestCase(unittest.TestCase):
    def setUp(self) -> None:
        _reset_auth_globals()
        self.temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(self.temp_dir.name)
        self.db_path = data_dir / "personal_quant_test.db"
        env_path = data_dir / ".env"
        env_path.write_text(
            "\n".join(
                [
                    "STOCK_LIST=600519",
                    "GEMINI_API_KEY=test",
                    "ADMIN_AUTH_ENABLED=false",
                    "PORTFOLIO_RISK_CONCENTRATION_ALERT_PCT=70.0",
                    "PORTFOLIO_RISK_DRAWDOWN_ALERT_PCT=10.0",
                    "PORTFOLIO_RISK_STOP_LOSS_ALERT_PCT=25.0",
                    "PORTFOLIO_RISK_STOP_LOSS_NEAR_RATIO=0.8",
                    "PORTFOLIO_RISK_LOOKBACK_DAYS=0",
                    f"DATABASE_PATH={self.db_path}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(env_path)
        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        self.portfolio_service = PortfolioService()
        self.risk_service = PortfolioRiskService(portfolio_service=self.portfolio_service)
        self.dashboard_service = PersonalQuantService(
            portfolio_service=self.portfolio_service,
            risk_service=self.risk_service,
        )
        self.client = None
        if TestClient is not None:
            from api.app import create_app

            self.client = TestClient(create_app(static_dir=data_dir / "empty-static"))
        self._board_fetch_patcher = patch.object(PortfolioRiskService, "_fetch_belong_boards", return_value=[])
        self._board_fetch_patcher.start()

    def tearDown(self) -> None:
        self._board_fetch_patcher.stop()
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    def _save_close(self, symbol: str, on_date: date, close: float) -> None:
        df = pd.DataFrame(
            [
                {
                    "date": on_date,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1.0,
                    "amount": close,
                    "pct_chg": 0.0,
                }
            ]
        )
        self.db.save_daily_data(df, code=symbol, data_source="personal-quant-test")

    def _create_margin_position(self) -> int:
        account = self.portfolio_service.create_account(
            name="Eastmoney Margin",
            broker="eastmoney",
            market="cn",
            base_currency="CNY",
            account_type="margin",
            financing_debt=8000.0,
            min_maintenance_ratio=1.5,
        )
        account_id = account["id"]
        self.portfolio_service.record_cash_ledger(
            account_id=account_id,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=5000.0,
            currency="CNY",
        )
        self.portfolio_service.record_trade(
            account_id=account_id,
            symbol="600519",
            trade_date=date(2026, 1, 1),
            side="buy",
            quantity=100,
            price=100.0,
            market="cn",
            currency="CNY",
        )
        self._save_close("600519", date(2026, 1, 1), 100.0)
        return account_id

    def test_margin_account_metadata_flows_into_snapshot_and_risk(self) -> None:
        account_id = self._create_margin_position()

        snapshot = self.portfolio_service.get_portfolio_snapshot(
            account_id=account_id,
            as_of=date(2026, 1, 1),
            cost_method="fifo",
            include_realtime=False,
        )
        account = snapshot["accounts"][0]
        self.assertEqual(account["account_type"], "margin")
        self.assertEqual(account["financing_debt"], 8000.0)
        self.assertAlmostEqual(account["maintenance_ratio"], 1.875, places=6)
        self.assertEqual(account["net_asset"], 7000.0)

        risk = self.risk_service.get_risk_report(
            account_id=account_id,
            as_of=date(2026, 1, 1),
            cost_method="fifo",
            include_realtime=False,
        )
        self.assertEqual(risk["margin_risk"]["margin_account_count"], 1)
        self.assertEqual(risk["margin_risk"]["worst_level"], "watch")
        self.assertFalse(risk["margin_risk"]["alert"])

    def test_personal_dashboard_summarizes_accounts_and_actions(self) -> None:
        self._create_margin_position()

        dashboard = self.dashboard_service.get_dashboard(
            as_of=date(2026, 1, 1),
            cost_method="fifo",
            include_realtime=False,
        )

        self.assertEqual(dashboard["summary"]["account_count"], 1)
        self.assertEqual(dashboard["summary"]["margin_account_count"], 1)
        self.assertEqual(dashboard["summary"]["financing_debt"], 8000.0)
        self.assertEqual(dashboard["summary"]["net_asset"], 7000.0)
        self.assertEqual(dashboard["accounts"][0]["margin_level"], "watch")
        self.assertEqual(dashboard["top_positions"][0]["symbol"], "600519")
        self.assertTrue(any(item["name"] == "东方财富两融" for item in dashboard["account_templates"]))
        self.assertTrue(any(item["action"] == "no_plan_no_trade" for item in dashboard["action_plan"]))

    def test_personal_dashboard_endpoint_returns_default_templates(self) -> None:
        if self.client is None:
            self.skipTest("fastapi is not installed in this environment")
        response = self.client.get(
            "/api/v1/portfolio/personal-dashboard",
            params={"as_of": "2026-01-01", "cost_method": "fifo", "include_realtime": "false"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["summary"]["account_count"], 0)
        self.assertTrue(any(item["name"] == "华宝证券普通" for item in payload["account_templates"]))
        self.assertEqual(payload["action_plan"][0]["action"], "create_default_accounts")


if __name__ == "__main__":
    unittest.main()
