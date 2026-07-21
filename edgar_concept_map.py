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

    # ── Financial Firm Metrics (banks/insurers) — punch list #36 ──────────────
    # Standard FCF/ROIC/Gross-Margin/Debt-FCF metrics above don't describe a
    # bank or insurer's business model (their "inventory" is money; leverage
    # is the business, not something to penalize). These raw fields feed a
    # separate scoring path — evaluate_financial_firm_funnel() and
    # score_financial_firm_breakdown() in sec_utils.py — used only for
    # tickers classified as "bank" or "insurance" via classify_financial_
    # subtype() below. Every tag here was verified against real filings
    # (JPMorgan Chase CIK 0000019617, Progressive Corp CIK 0000080661) before
    # being added, not guessed from the XBRL taxonomy docs alone.

    "interest_income": [
        # Bank tag: total interest + dividend income on loans/securities/etc.
        "InterestAndDividendIncomeOperating",
        "InterestIncomeOperating",
        "InterestAndFeeIncomeLoansAndLeases",
    ],

    "noninterest_income": [
        "NoninterestIncome",
    ],

    "noninterest_expense": [
        "NoninterestExpense",
    ],

    "provision_credit_losses": [
        "ProvisionForLoanLeaseAndOtherLosses",
        "ProvisionForLoanAndLeaseLosses",
        # CECL-era tag (post-2020 for most bank filers)
        "ProvisionForCreditLossExpenseReversal",
        # Non-bank fallback (older filers, finance subsidiaries)
        "ProvisionForDoubtfulAccounts",
    ],

    "premiums_earned": [
        "PremiumsEarnedNetPropertyAndCasualty",
        "PremiumsEarnedNet",
    ],

    "policyholder_benefits": [
        "PolicyholderBenefitsAndClaimsIncurredNet",
        "PolicyholderBenefitsAndClaimsIncurredGross",
        "IncurredClaimsPropertyCasualtyAndLiability",
    ],

    "underwriting_expenses": [
        "OtherUnderwritingExpense",
        "PolicyAcquisitionCostsAndOtherInsuranceExpense",
        "AmortizationOfDeferredPolicyAcquisitionCosts",
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
    # ── Financial firm derived fields (#36) — computed only for bank/insurer
    # subtypes, in place of the metrics above which don't apply to them.
    "roe":              "net_income / total_equity",
    "equity_to_assets": "total_equity / total_assets  (capital cushion / leverage proxy)",
    "net_interest_income": "interest_income - interest_expense",
    "nim_proxy":        "net_interest_income / total_assets  (proxy — EDGAR has no "
                         "'average earning assets' tag, so total assets stands in; "
                         "labeled as an approximation everywhere it's shown)",
    "efficiency_ratio": "noninterest_expense / (net_interest_income + noninterest_income)  "
                         "(bank cost discipline — lower is better)",
    "provision_to_ni":  "provision_credit_losses / net_income  (credit cost as a share of earnings)",
    "combined_ratio":   "(policyholder_benefits + underwriting_expenses) / premiums_earned  "
                         "(insurer underwriting profitability — under 100% = underwriting profit)",
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


# ── Financial firm subtypes — banks & insurers ─────────────────────────────
# Subsets of FINANCIAL_SIC_CODES with an alternative scoring path actually
# built (#36 v1, scoped to Buffett/Berkshire's own playbook — banks and
# insurers). Other financial SIC codes above (brokers, real estate,
# investment offices, REITs) are still flagged is_financial=True and still
# respect the Market Screener "skip financial firms" toggle, but don't get
# an alt score yet — each needs its own metric set (AUM-based for brokers,
# FFO for REITs) not built here. Revisit as a future punch list item if
# wanted.

BANK_SIC_CODES = {
    "6020", "6021", "6022",  # State & national commercial banks
    "6035", "6036",          # Savings institutions
    "6099",                  # Functions related to depository banking
    "6110", "6120", "6141",  # Personal/mortgage/consumer credit
    "6153", "6159",          # Short-term business credit
    "6199",                  # Finance services
}

INSURANCE_SIC_CODES = {
    "6311", "6321", "6324",  # Insurance carriers
    "6331", "6351", "6361",  # Fire, marine, casualty insurance
    "6411",                  # Insurance agents
}


def classify_financial_subtype(sic: str):
    """
    Returns "bank", "insurance", "other_financial", or None (not a
    financial firm at all) for a given 4-digit SIC code string.

    "other_financial" covers brokers, real estate, investment offices, and
    REITs — is_financial is True for these but there's no alt scoring path
    for them yet, so callers should fall back to the existing skip/exclude
    behavior rather than trying to score them under the bank or insurer
    framework (neither fits).
    """
    if not sic:
        return None
    if sic in BANK_SIC_CODES:
        return "bank"
    if sic in INSURANCE_SIC_CODES:
        return "insurance"
    if sic in FINANCIAL_SIC_CODES:
        return "other_financial"
    return None


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
