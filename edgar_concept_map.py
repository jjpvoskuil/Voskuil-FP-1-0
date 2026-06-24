"""
edgar_concept_map.py — Canonical XBRL concept → scoring field map for Voskuil FP 1.0

Each scoring field lists candidate XBRL concept tags in priority order.
fetch_company_facts() tries each in sequence and uses the first one found.

Design principles:
- One map, used consistently across all years and all pages (Equity Scout,
  Dashboard, Market Screener). Never patch per-company — fix the map itself.
- Historical pulls use the same map so normalization is fully replayable.
- Concept aliases handled here, not scattered across page files.
- Comments explain why each alias exists (different GAAP tagging eras, etc.)

Scoring field → list of XBRL concept candidates (tried in order):
"""

CONCEPT_MAP = {

    # ── Cash Flow Statement ──────────────────────────────────────────────────

    "op_cf": [
        # Standard since ~2009 XBRL mandate
        "NetCashProvidedByUsedInOperatingActivities",
        # Older tag used pre-2010 by some filers
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],

    "inv_cf": [
        "NetCashProvidedByUsedInInvestingActivities",
        "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations",
    ],

    "capex": [
        # Most common: negative number (cash outflow)
        "PaymentsToAcquirePropertyPlantAndEquipment",
        # Some filers use this alias
        "PaymentsForCapitalImprovements",
        # Rare but used by some retailers/industrials
        "PaymentsToAcquireProductiveAssets",
    ],

    "dna": [
        # D&A as reported in cash flow statement (add-back to net income)
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        # Some filers break it out separately
        "Depreciation",
    ],

    "interest_paid": [
        # Cash basis — preferred for coverage ratio (punch list #35)
        "InterestPaid",
        "InterestPaidNet",
        # Fallback: accrual basis (less accurate but widely available)
        "InterestExpense",
        "InterestExpenseOperating",
    ],

    # ── Income Statement ─────────────────────────────────────────────────────

    "revenue": [
        # Most universally reliable — used by the majority of filers across all eras
        "Revenues",
        # Legacy net sales tag — common pre-2018
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        # ASC 606 tag (post-2018 revenue recognition standard)
        # Listed after Revenues because some filers tag BOTH and the ASC 606
        # tag sometimes includes segment subtotals that cause double-counting
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        # NOTE: RevenueFromContractWithCustomerExcludingAssessedTaxAbstract
        # intentionally excluded — Abstract concepts are XBRL structural
        # containers, not reported values, and return garbage data
    ],

    "gross_profit": [
        "GrossProfit",
    ],

    "cost_of_revenue": [
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
        # Some tech companies use this
        "CostOfRevenueExcludingDepreciationAndAmortization",
    ],

    "op_income": [
        "OperatingIncomeLoss",
        # Some filers use income from operations
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    ],

    "net_income": [
        "NetIncomeLoss",
        # Excludes noncontrolling interest — preferred for owner earnings
        "NetIncomeLossAvailableToCommonStockholdersBasic",
        "NetIncomeLossAttributableToParent",
    ],

    "interest_expense": [
        # Accrual basis — used as fallback when interest_paid unavailable
        "InterestExpense",
        "InterestExpenseOperating",
        "InterestAndDebtExpense",
    ],

    "income_tax": [
        "IncomeTaxExpenseBenefit",
    ],

    "diluted_shares": [
        "CommonStockSharesOutstanding",
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingDiluted",
    ],

    "eps_diluted": [
        "EarningsPerShareDiluted",
    ],

    # ── Balance Sheet ────────────────────────────────────────────────────────

    "total_assets": [
        "Assets",
    ],

    "total_equity": [
        # Excludes noncontrolling interest — correct for invested capital calc
        "StockholdersEquity",
        "StockholdersEquityAttributableToParent",
        # Includes noncontrolling interest — use if above not found
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],

    "long_term_debt": [
        # Outstanding principal on long-term borrowings
        "LongTermDebt",
        "LongTermDebtNoncurrent",
        # Some filers net out current portion separately
        "LongTermDebtAndCapitalLeaseObligations",
        # Notes payable / debentures used by older filers
        "NotesPayable",
        "SeniorNotes",
    ],

    "short_term_debt": [
        "ShortTermBorrowings",
        "DebtCurrent",
        "NotesPayableCurrent",
        "LongTermDebtCurrent",
    ],

    "total_liabilities": [
        "Liabilities",
    ],

    "current_assets": [
        "AssetsCurrent",
    ],

    "current_liabilities": [
        "LiabilitiesCurrent",
    ],

    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsAndShortTermInvestments",
        "Cash",
    ],

    "goodwill": [
        "Goodwill",
    ],

    "intangibles": [
        "FiniteLivedIntangibleAssetsNet",
        "IntangibleAssetsNetExcludingGoodwill",
    ],

    "retained_earnings": [
        "RetainedEarningsAccumulatedDeficit",
    ],

    "inventory": [
        "InventoryNet",
        "Inventories",
    ],

    "accounts_receivable": [
        "AccountsReceivableNetCurrent",
        "ReceivablesNetCurrent",
    ],

    "accounts_payable": [
        "AccountsPayableCurrent",
    ],

    "ppe_net": [
        # Net property, plant & equipment — used for invested capital
        "PropertyPlantAndEquipmentNet",
    ],

}


# ── Derived field definitions ─────────────────────────────────────────────────
# These are computed from the raw XBRL fields above, not fetched directly.
# Documented here so the logic is in one place.

DERIVED_FIELDS = {
    "fcf":           "op_cf + inv_cf  (investing CF is negative, so this subtracts capex proxy)",
    "invested_cap":  "total_equity + long_term_debt + short_term_debt",
    "roic":          "net_income / invested_cap",
    "debt_to_fcf":   "(long_term_debt + short_term_debt) / fcf",
    "gross_margin":  "gross_profit / revenue",
    "int_coverage":  "op_income / interest_paid  (or interest_expense as fallback)",
    "owner_earn":    "net_income + dna - capex  (Buffett definition)",
    "net_debt":      "long_term_debt + short_term_debt - cash",
}


# ── Financial firm SIC codes ──────────────────────────────────────────────────
# These use fundamentally different accounting — flag for alternative scoring.
# Pairs with punch list #36.

FINANCIAL_SIC_CODES = {
    "6020", "6021", "6022",  # State & national commercial banks
    "6035", "6036",          # Savings institutions
    "6099",                  # Functions related to depository banking
    "6110", "6120", "6141",  # Personal/mortgage/consumer credit
    "6153", "6159",          # Short-term business credit
    "6199",                  # Finance services
    "6200",                  # Security & commodity brokers
    "6211",                  # Security brokers & dealers
    "6282",                  # Investment advice
    "6311", "6321", "6324",  # Insurance carriers
    "6331", "6351", "6361",  # Fire, marine, casualty insurance
    "6411",                  # Insurance agents
    "6500", "6510", "6512",  # Real estate
    "6552",                  # Land subdividers & developers
    "6726",                  # Investment offices (holding companies)
    "6798",                  # REITs
}


# ── Cyclical firm SIC codes ───────────────────────────────────────────────────
# Single-period scoring unreliable — flag for full-cycle analysis caveat.
# Pairs with punch list #37.

CYCLICAL_SIC_CODES = {
    "1000", "1040", "1090",  # Metal mining
    "1311", "1381", "1382",  # Oil & gas extraction / services
    "2600", "2611", "2621",  # Paper & allied products
    "2800", "2810", "2819",  # Chemicals
    "2911",                  # Petroleum refining
    "3310", "3312", "3317",  # Steel & iron
    "3330", "3334",          # Primary nonferrous metals
    "3559", "3560",          # Industrial machinery
    "3710", "3711", "3714",  # Motor vehicles & parts
    "3720", "3721",          # Aircraft
    "4911", "4931", "4941",  # Electric, gas, water utilities
    "5150", "5160",          # Farm products & chemicals wholesale
    "5170",                  # Petroleum wholesale
}
