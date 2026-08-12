# Personal Quant Dashboard

The personal quant layer extends the existing DSA portfolio workflow instead of creating a second portfolio ledger.

It adds account metadata for:

- `account_type`: `cash` or `margin`
- `financing_debt`: current financing liability in the account base currency
- `min_maintenance_ratio`: the account-specific maintenance floor

The portfolio snapshot now exposes per-account `net_asset` and `maintenance_ratio`. The portfolio risk report adds a `margin_risk` block with account-level levels: `safe`, `watch`, `warning`, `danger`, or `unknown`.

`GET /api/v1/portfolio/personal-dashboard` returns a consolidated personal dashboard with:

- default account templates for Eastmoney margin, Huabao cash, and Huabao margin accounts
- total equity, financing debt, net asset, cash, market value, and margin account count
- top holdings across accounts
- risk events from margin, concentration, drawdown, stop-loss, and defensive AI signals
- a daily action plan anchored by the `no_plan_no_trade` principle

This endpoint is read-only. It does not place orders, change positions, or replace the existing trade/cash/corporate-action source of truth.
