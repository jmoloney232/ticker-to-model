# Spec 02 — Canonical data schema

The exact list of financial line items and their XBRL fallback tag chains. The
machine-readable source of truth is [`backend/ingest/schema.yaml`](../backend/ingest/schema.yaml);
this spec explains the design and mirrors it for review. **The YAML wins on any
discrepancy** (a CI check will compare them once code exists).

**Status: proposal for owner review.** Chains are built from common large-cap filing
practice; phase 1 verifies every chain empirically against the five fixture
`companyfacts` payloads. Expect tags to be *added*; the no-silent-zero rules never
weaken.

## Inputs

- The raw `companyfacts` fact set for one company (spec 01 fetches and pre-filters it).

## Outputs

- The canonical item definitions used by the mapping step (spec 01 §5): 65 line items
  (18 income statement, 32 balance sheet, 15 cash flow), each with an ordered tag chain
  and a documented missing-value rule.

## Design rules

1. **Ordered fallback chains.** Companies tag the same concept inconsistently (ASC 606
   adopters vs older years, deprecated taxonomy tags, split vs combined line items).
   Each canonical item lists tags in preference order; the first with a fact for the
   period wins, and the winner is recorded as provenance.
2. **No silent zeros.** `required: true` + unmappable → hard error naming the item and
   tags tried. Optional + unmappable → one of three documented `missing_rule`s:
   `zero_logged` (0 + warning), `derive` (computed per expression, provenance
   "derived"), `omit` (absent; dependent checks report "skipped").
3. **Residual buckets keep statements tied.** `other_operating`, `other_current_assets`,
   `other_noncurrent_liabilities` etc. are defined as residuals against reported
   totals, so the mapped statements reconcile exactly to what the company filed instead
   of leaking mapping gaps into validation noise.
4. **Cross-checks where redundancy exists.** When both a reported aggregate and a
   derivable one are present (gross profit, total liabilities, current assets), both
   are computed and compared (H5, spec 07).
5. **Chain-consistency for cash.** The restricted-cash ASU (2016-18) means "cash" can
   include restricted cash under the newer total tags. Whatever chain entry wins for
   the balance-sheet cash item, the cash-flow Δ tag and H2 must use the *same*
   definition — mismatching them creates fake tie-out failures.
6. **Sign conventions are explicit.** `Payments*` tags arrive positive (outflows);
   `IncreaseDecrease*` tags follow taxonomy sign definitions. Normalization happens at
   mapping time with per-tag sign tests.
7. **Leases:** finance leases are debt (included in ST/LT debt when tagged); operating
   leases are tracked separately, excluded from debt (consistent with unadjusted
   EBITDA), and drive the `lease_heavy` warning.

## Line items (mirror of schema.yaml — tags are `us-gaap:` unless noted)

**Req** ✓ = required (unmappable → hard error). **Missing** = rule for optional items.

### Income statement (durations)

| Item | Req | Tag chain | Missing |
|---|---|---|---|
| revenue | ✓ | RevenueFromContractWithCustomerExcludingAssessedTax → Revenues → SalesRevenueNet → RevenueFromContractWithCustomerIncludingAssessedTax | — |
| cost_of_revenue | ✓ | CostOfRevenue → CostOfGoodsAndServicesSold → CostOfGoodsSold | — |
| gross_profit | | GrossProfit | derive: revenue − COGS (+ cross-check) |
| research_and_development | | ResearchAndDevelopmentExpense | zero_logged |
| selling_general_admin | | SellingGeneralAndAdministrativeExpense | derive: Selling + G&A pieces |
| other_operating | | OtherOperatingIncomeExpenseNet | derive: residual to reported EBIT |
| operating_income | ✓ | OperatingIncomeLoss | — (+ cross-check) |
| interest_expense | | InterestExpense → InterestExpenseNonoperating → InterestAndDebtExpense → InterestExpenseDebt | zero_logged |
| interest_income | | InvestmentIncomeInterest → InvestmentIncomeInterestAndDividend | zero_logged |
| other_nonoperating | | NonoperatingIncomeExpense → OtherNonoperatingIncomeExpense | derive: residual to pretax |
| pretax_income | ✓ | IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest → …MinorityInterestAndIncomeLossFromEquityMethodInvestments | — |
| income_tax | ✓ | IncomeTaxExpenseBenefit | — |
| net_income | ✓ | NetIncomeLoss → ProfitLoss (subtract NCI when this wins) | — |
| nci_income | | NetIncomeLossAttributableToNoncontrollingInterest | zero_logged |
| eps_basic / eps_diluted | | EarningsPerShareBasic / EarningsPerShareDiluted | omit |
| shares_basic_wa | ✓ | WeightedAverageNumberOfSharesOutstandingBasic | — |
| shares_diluted_wa | | WeightedAverageNumberOfDilutedSharesOutstanding | derive: = basic (no dilution) |

### Balance sheet (instants)

| Item | Req | Tag chain | Missing |
|---|---|---|---|
| cash_and_equivalents | ✓ | CashAndCashEquivalentsAtCarryingValue → CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents | — (chain-consistency rule 5) |
| short_term_investments | | ShortTermInvestments → OtherShortTermInvestments → MarketableSecuritiesCurrent → AvailableForSaleSecuritiesDebtSecuritiesCurrent | zero_logged |
| accounts_receivable | | AccountsReceivableNetCurrent → ReceivablesNetCurrent | zero_logged |
| inventory | | InventoryNet | zero_logged |
| other_current_assets | | OtherAssetsCurrent | derive: residual |
| total_current_assets | | AssetsCurrent | derive: sum (unclassified BS → warn) |
| ppe_net | | PropertyPlantAndEquipmentNet → PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAsset… | zero_logged (D&A default degrades to %-of-revenue) |
| goodwill | | Goodwill | zero_logged |
| intangibles | | IntangibleAssetsNetExcludingGoodwill → FiniteLivedIntangibleAssetsNet | zero_logged |
| long_term_investments | | LongTermInvestments → MarketableSecuritiesNoncurrent | zero_logged |
| operating_lease_rou | | OperatingLeaseRightOfUseAsset | zero_logged |
| other_noncurrent_assets | | OtherAssetsNoncurrent | derive: residual |
| total_assets | ✓ | Assets | — |
| accounts_payable | | AccountsPayableCurrent → AccountsPayableTradeCurrent → AccountsPayableAndAccruedLiabilitiesCurrent (combined → accrued forced 0, warned) | zero_logged |
| accrued_liabilities | | AccruedLiabilitiesCurrent | zero_logged |
| short_term_debt | | DebtCurrent | derive: ShortTermBorrowings + CommercialPaper + first_of(LongTermDebtCurrent + FinanceLeaseLiabilityCurrent, LongTermDebtAndCapitalLeaseObligationsCurrent) |
| deferred_revenue_current | | ContractWithCustomerLiabilityCurrent → DeferredRevenueCurrent | zero_logged |
| other_current_liabilities | | OtherLiabilitiesCurrent | derive: residual |
| total_current_liabilities | | LiabilitiesCurrent | derive: sum (warn) |
| long_term_debt | | LongTermDebtNoncurrent → LongTermDebtAndCapitalLeaseObligations (fallback LongTermDebt − current portion; + FinanceLeaseLiabilityNoncurrent for the plain variant only) | zero_logged |
| operating_lease_liability | | OperatingLeaseLiabilityCurrent + Noncurrent → OperatingLeaseLiability | zero_logged |
| deferred_tax_liabilities | | DeferredIncomeTaxLiabilitiesNet → DeferredTaxLiabilities | zero_logged |
| pension_liability | | PensionAndOtherPostretirementDefinedBenefitPlansLiabilitiesNoncurrent → DefinedBenefitPensionPlanLiabilitiesNoncurrent | zero_logged |
| other_noncurrent_liabilities | | OtherLiabilitiesNoncurrent | derive: residual |
| total_liabilities | | Liabilities | derive: L&E − equity − NCI − temp equity (+ cross-check) |
| preferred_equity | | PreferredStockValue | zero_logged (usually inside equity — H1 must not double-count) |
| temporary_equity | | TemporaryEquityCarryingAmountAttributableToParent → RedeemableNoncontrollingInterestEquityCarryingAmount | zero_logged (mezzanine; without it H1 falsely fails on redeemable-NCI filers) |
| stockholders_equity | ✓ | StockholdersEquity → StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest (− NCI) | — |
| noncontrolling_interest | | MinorityInterest | zero_logged |
| retained_earnings | | RetainedEarningsAccumulatedDeficit | omit (H4 → skipped) |
| total_liabilities_and_equity | | LiabilitiesAndStockholdersEquity | derive: sum |
| shares_outstanding | ✓ | **dei:**EntityCommonStockSharesOutstanding | — (share-proxy input; `selection: latest` — most recent fact across all forms, incl. 10-Qs) |

### Cash flow statement (durations)

| Item | Req | Tag chain | Missing |
|---|---|---|---|
| d_and_a | ✓ | DepreciationDepletionAndAmortization → DepreciationAmortizationAndAccretionNet → Depreciation + AmortizationOfIntangibleAssets | — |
| stock_compensation | | ShareBasedCompensation | zero_logged |
| deferred_taxes_cf | | DeferredIncomeTaxExpenseBenefit → DeferredIncomeTaxesAndTaxCredits | zero_logged |
| working_capital_change | | IncreaseDecreaseInOperatingCapital | derive: CFO − NI − non-cash items |
| cash_from_operations | ✓ | NetCashProvidedByUsedInOperatingActivities → …ContinuingOperations | — |
| capex | ✓ | PaymentsToAcquirePropertyPlantAndEquipment → PaymentsToAcquireProductiveAssets | — |
| acquisitions | | PaymentsToAcquireBusinessesNetOfCashAcquired | zero_logged |
| cash_from_investing | ✓ | NetCashProvidedByUsedInInvestingActivities → …ContinuingOperations | — |
| dividends_paid | | PaymentsOfDividends → PaymentsOfDividendsCommonStock | zero_logged |
| buybacks | | PaymentsForRepurchaseOfCommonStock | zero_logged |
| debt_issued / debt_repaid | | ProceedsFromIssuanceOfLongTermDebt / RepaymentsOfLongTermDebt | zero_logged |
| cash_from_financing | ✓ | NetCashProvidedByUsedInFinancingActivities → …ContinuingOperations | — |
| fx_effect | | EffectOfExchangeRateOnCashAndCashEquivalents → …RestrictedCashAndRestrictedCashEquivalents | zero_logged |
| net_change_in_cash | | CashAndCashEquivalentsPeriodIncreaseDecrease → …IncludingExchangeRateEffect | derive: CFO+CFI+CFF+FX |

## Invariants

- Every canonical item has ≥1 tag or a derive expression; required items always have
  tags (a required item may not depend solely on derivation).
- Missing rules are one of exactly {zero_logged, derive, omit} — no ad-hoc behavior.
- Residual items always reference a reported total, so mapped statements reconcile to
  filings exactly.
- The YAML and this spec agree item-for-item (CI-checked in phase 1).

## Error cases

Owned by spec 01 (`MissingRequiredItemError`, unmapped warnings). This spec's failure
mode is **schema drift** — YAML vs spec vs code — covered by the CI comparison.

## How tested

- Phase 1 **chain-coverage test**: for each fixture ticker and each canonical item,
  assert which chain entry wins per year and snapshot it — chains are then evidence,
  not guesses. Any item resolving to `zero_logged` on a fixture where the concept
  plainly exists (e.g. inventory at COST) fails the test.
- Sign-convention tests per `IncreaseDecrease*`/`Payments*` tag used.
- Schema-file validation test: YAML parses; every item has name/statement/shape/
  required + (tags or derive); missing_rule present iff not required.
- H5 cross-checks (spec 07) act as the runtime guard for wrong-tag mappings.
