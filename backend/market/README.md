# market — market-data provider interface

Single `MarketDataProvider` interface hiding the vendors: Alpaca (split-adjusted bars,
prices), FRED (10Y Treasury), plus the in-house beta computation (2y weekly OLS vs SPY).
Swap a vendor by replacing one adapter module. Global benchmark cache lives here. Spec:
[`specs/03-market-data.md`](../../specs/03-market-data.md). Built in phase 2.
