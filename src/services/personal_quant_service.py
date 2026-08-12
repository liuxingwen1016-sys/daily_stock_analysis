# -*- coding: utf-8 -*-
"""Personal quant dashboard on top of DSA portfolio primitives."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from src.services.portfolio_risk_service import PortfolioRiskService
from src.services.portfolio_service import PortfolioService


PERSONAL_ACCOUNT_TEMPLATES = [
    {"name": "东方财富两融", "broker": "eastmoney", "account_type": "margin", "market": "cn", "base_currency": "CNY"},
    {"name": "华宝证券普通", "broker": "huabao", "account_type": "cash", "market": "cn", "base_currency": "CNY"},
    {"name": "华宝证券两融", "broker": "huabao", "account_type": "margin", "market": "cn", "base_currency": "CNY"},
]

PERSONAL_PRINCIPLES = [
    {"key": "no_plan_no_trade", "label": "No plan, no trade", "severity": "hard"},
    {"key": "respect_margin_floor", "label": "Keep margin buffer above the account floor", "severity": "hard"},
    {"key": "avoid_single_position_overload", "label": "Review any single holding above 35% market value", "severity": "soft"},
    {"key": "daily_review", "label": "Review account, risk and AI signal changes every trading day", "severity": "soft"},
]


class PersonalQuantService:
    """Build a personal trading dashboard from portfolio and decision signals."""

    def __init__(
        self,
        *,
        portfolio_service: Optional[PortfolioService] = None,
        risk_service: Optional[PortfolioRiskService] = None,
    ):
        self.portfolio_service = portfolio_service or PortfolioService()
        self.risk_service = risk_service or PortfolioRiskService(portfolio_service=self.portfolio_service)

    def get_dashboard(
        self,
        *,
        account_id: Optional[int] = None,
        as_of: Optional[date] = None,
        cost_method: str = "fifo",
        include_realtime: bool = True,
    ) -> Dict[str, Any]:
        as_of_date = as_of or date.today()
        snapshot = self.portfolio_service.get_portfolio_snapshot(
            account_id=account_id,
            as_of=as_of_date,
            cost_method=cost_method,
            include_realtime=include_realtime,
        )
        risk = self.risk_service.get_risk_report(
            account_id=account_id,
            as_of=as_of_date,
            cost_method=cost_method,
            include_realtime=include_realtime,
        )

        accounts = self._build_accounts(snapshot, as_of_date=as_of_date)
        top_positions = self._build_top_positions(snapshot)
        risk_events = self._build_risk_events(risk)

        return {
            "as_of": snapshot["as_of"],
            "cost_method": snapshot["cost_method"],
            "currency": snapshot["currency"],
            "summary": self._build_summary(snapshot, accounts),
            "principles": PERSONAL_PRINCIPLES,
            "account_templates": PERSONAL_ACCOUNT_TEMPLATES,
            "accounts": accounts,
            "top_positions": top_positions,
            "risk_events": risk_events,
            "action_plan": self._build_action_plan(snapshot, risk, risk_events),
        }

    def _build_accounts(self, snapshot: Dict[str, Any], *, as_of_date: date) -> List[Dict[str, Any]]:
        risk_margin = {}
        try:
            risk_margin = self.risk_service._build_margin_risk(snapshot)
        except Exception:
            risk_margin = {"accounts": []}
        margin_risk_by_account = {
            item.get("account_id"): item
            for item in (risk_margin.get("accounts") or [])
        }

        accounts: List[Dict[str, Any]] = []
        for account in snapshot.get("accounts", []) or []:
            debt = float(account.get("financing_debt") or 0.0)
            currency = str(account.get("base_currency") or snapshot.get("currency") or "CNY")
            debt_base, fx_stale, _ = self.portfolio_service.convert_amount(
                amount=debt,
                from_currency=currency,
                to_currency=str(snapshot.get("currency") or "CNY"),
                as_of_date=as_of_date,
            )
            risk_item = margin_risk_by_account.get(account.get("account_id"), {})
            accounts.append({
                "account_id": account.get("account_id"),
                "account_name": account.get("account_name"),
                "broker": account.get("broker"),
                "market": account.get("market"),
                "currency": currency,
                "account_type": account.get("account_type") or "cash",
                "total_cash": account.get("total_cash"),
                "total_market_value": account.get("total_market_value"),
                "total_equity": account.get("total_equity"),
                "financing_debt": debt,
                "financing_debt_base": round(debt_base, 6),
                "net_asset": account.get("net_asset"),
                "maintenance_ratio": account.get("maintenance_ratio"),
                "min_maintenance_ratio": account.get("min_maintenance_ratio"),
                "margin_level": risk_item.get("level", "safe"),
                "margin_message": risk_item.get("message", ""),
                "fx_stale": bool(account.get("fx_stale")) or fx_stale,
                "position_count": len(account.get("positions") or []),
            })
        return accounts

    @staticmethod
    def _build_summary(snapshot: Dict[str, Any], accounts: List[Dict[str, Any]]) -> Dict[str, Any]:
        total_debt = sum(float(item.get("financing_debt_base") or 0.0) for item in accounts)
        total_equity = float(snapshot.get("total_equity") or 0.0)
        total_net_asset = total_equity - total_debt
        margin_accounts = [
            item for item in accounts
            if item.get("account_type") == "margin" or float(item.get("financing_debt") or 0.0) > 0
        ]
        return {
            "account_count": int(snapshot.get("account_count") or 0),
            "margin_account_count": len(margin_accounts),
            "total_cash": snapshot.get("total_cash"),
            "total_market_value": snapshot.get("total_market_value"),
            "total_equity": round(total_equity, 6),
            "financing_debt": round(total_debt, 6),
            "net_asset": round(total_net_asset, 6),
            "unrealized_pnl": snapshot.get("unrealized_pnl"),
            "fx_stale": bool(snapshot.get("fx_stale")),
        }

    @staticmethod
    def _build_top_positions(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        total_mv = float(snapshot.get("total_market_value") or 0.0)
        for account in snapshot.get("accounts", []) or []:
            for position in account.get("positions", []) or []:
                market_value = float(position.get("market_value_base") or 0.0)
                rows.append({
                    "account_id": account.get("account_id"),
                    "account_name": account.get("account_name"),
                    "symbol": position.get("symbol"),
                    "market": position.get("market"),
                    "quantity": position.get("quantity"),
                    "market_value_base": round(market_value, 6),
                    "weight_pct": round((market_value / total_mv * 100.0) if total_mv > 0 else 0.0, 4),
                    "unrealized_pnl_pct": position.get("unrealized_pnl_pct"),
                    "price_stale": bool(position.get("price_stale")),
                })
        rows.sort(key=lambda item: float(item.get("market_value_base") or 0.0), reverse=True)
        return rows[:10]

    @staticmethod
    def _build_risk_events(risk: Dict[str, Any]) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        margin = risk.get("margin_risk") or {}
        for item in margin.get("accounts", []) or []:
            if item.get("level") in {"watch", "warning", "danger", "unknown"}:
                events.append({
                    "type": "margin",
                    "severity": item.get("level"),
                    "account_id": item.get("account_id"),
                    "symbol": None,
                    "message": item.get("message"),
                })
        if (risk.get("concentration") or {}).get("alert"):
            events.append({
                "type": "concentration",
                "severity": "warning",
                "message": "Top position concentration is above threshold",
            })
        if (risk.get("drawdown") or {}).get("alert"):
            events.append({
                "type": "drawdown",
                "severity": "warning",
                "message": "Portfolio drawdown is above threshold",
            })
        for item in (risk.get("stop_loss") or {}).get("items", []) or []:
            events.append({
                "type": "stop_loss",
                "severity": "danger" if item.get("is_triggered") else "watch",
                "account_id": item.get("account_id"),
                "symbol": item.get("symbol"),
                "message": f"loss_pct={item.get('loss_pct')}",
            })
        for item in (risk.get("decision_signal_risk") or {}).get("items", []) or []:
            signal = item.get("signal") or {}
            events.append({
                "type": "ai_signal",
                "severity": "warning",
                "account_id": item.get("account_id"),
                "symbol": item.get("symbol"),
                "message": signal.get("action") or "defensive signal",
            })
        return events[:30]

    @staticmethod
    def _build_action_plan(
        snapshot: Dict[str, Any],
        risk: Dict[str, Any],
        risk_events: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        actions: List[Dict[str, Any]] = []
        if not snapshot.get("accounts"):
            actions.append({
                "priority": "high",
                "action": "create_default_accounts",
                "reason": "Set up Eastmoney margin, Huabao cash and Huabao margin accounts first",
            })
        if any(event.get("type") == "margin" and event.get("severity") in {"warning", "danger"} for event in risk_events):
            actions.append({
                "priority": "high",
                "action": "reduce_margin_pressure",
                "reason": "Maintenance ratio is near or below the configured floor",
            })
        if (risk.get("decision_signal_risk") or {}).get("total", 0) > 0:
            actions.append({
                "priority": "medium",
                "action": "review_defensive_ai_signals",
                "reason": "Held positions have active sell/reduce/alert signals",
            })
        if (risk.get("concentration") or {}).get("alert"):
            actions.append({
                "priority": "medium",
                "action": "review_position_concentration",
                "reason": "Single-name concentration is above the configured threshold",
            })
        actions.append({
            "priority": "daily",
            "action": "no_plan_no_trade",
            "reason": "Do not add or reduce positions without a written price, size and invalidation plan",
        })
        return actions
