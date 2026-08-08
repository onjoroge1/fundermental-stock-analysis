"""Canonical field → ordered us-gaap/dei tag priority map.

Companies tag the same concept differently across years. Each canonical field
lists tags in preference order; the selection policy in financial_periods.py
uses the first tag that yields a fact for a given period, and the raw tag is
preserved on every selected fact."""
from __future__ import annotations

# kind: "flow" (duration facts, income/cash-flow), "instant" (balance sheet),
#        "per_share" / "shares" (flow facts with non-USD units)
FIELD_MAP: dict[str, dict] = {
    # ---- income statement (flow, USD) ----
    "revenue": {"kind": "flow", "tags": [
        # banks/fintechs: total net revenue (NII + noninterest income);
        # ranked first — industrial filers never use this tag
        "RevenuesNetOfInterestExpense",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet"]},
    "cost_of_revenue": {"kind": "flow", "tags": [
        "CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"]},
    "gross_profit": {"kind": "flow", "tags": ["GrossProfit"]},
    "research_and_development": {"kind": "flow", "tags": [
        "ResearchAndDevelopmentExpense"]},
    "selling_general_and_administrative": {"kind": "flow", "tags": [
        "SellingGeneralAndAdministrativeExpense"]},
    "operating_income": {"kind": "flow", "tags": ["OperatingIncomeLoss"]},
    "interest_expense": {"kind": "flow", "tags": [
        "InterestExpense", "InterestExpenseNonoperating",
        "InterestIncomeExpenseNet"]},
    "pretax_income": {"kind": "flow", "tags": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]},
    "income_tax": {"kind": "flow", "tags": ["IncomeTaxExpenseBenefit"]},
    "net_income": {"kind": "flow", "tags": ["NetIncomeLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
        "ProfitLoss"]},  # ProfitLoss: incl. noncontrolling interests (AVGO)
    # ---- per-share / share counts ----
    "basic_eps": {"kind": "per_share", "tags": ["EarningsPerShareBasic"]},
    "diluted_eps": {"kind": "per_share", "tags": ["EarningsPerShareDiluted"]},
    "weighted_average_basic_shares": {"kind": "shares", "tags": [
        "WeightedAverageNumberOfSharesOutstandingBasic"]},
    "weighted_average_diluted_shares": {"kind": "shares", "tags": [
        "WeightedAverageNumberOfDilutedSharesOutstanding"]},
    # ---- balance sheet (instant, USD) ----
    "cash_and_equivalents": {"kind": "instant", "tags": [
        "CashAndCashEquivalentsAtCarryingValue"]},
    "marketable_securities_current": {"kind": "instant", "tags": [
        "MarketableSecuritiesCurrent", "ShortTermInvestments",
        "AvailableForSaleSecuritiesDebtSecuritiesCurrent"]},
    "marketable_securities_noncurrent": {"kind": "instant", "tags": [
        "MarketableSecuritiesNoncurrent",
        "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent",
        "LongTermInvestments"]},
    "accounts_receivable": {"kind": "instant", "tags": [
        "AccountsReceivableNetCurrent"]},
    "inventory": {"kind": "instant", "tags": ["InventoryNet"]},
    "current_assets": {"kind": "instant", "tags": ["AssetsCurrent"]},
    "property_plant_equipment": {"kind": "instant", "tags": [
        "PropertyPlantAndEquipmentNet"]},
    "goodwill": {"kind": "instant", "tags": ["Goodwill"]},
    "intangible_assets": {"kind": "instant", "tags": [
        "FiniteLivedIntangibleAssetsNet", "IntangibleAssetsNetExcludingGoodwill"]},
    "total_assets": {"kind": "instant", "tags": [
        "Assets",
        "LiabilitiesAndStockholdersEquity"]},  # identity fallback (banks)
    "accounts_payable": {"kind": "instant", "tags": ["AccountsPayableCurrent"]},
    "deferred_revenue": {"kind": "instant", "tags": [
        "ContractWithCustomerLiabilityCurrent", "DeferredRevenueCurrent"]},
    "current_liabilities": {"kind": "instant", "tags": ["LiabilitiesCurrent"]},
    "short_term_debt": {"kind": "instant", "tags": [
        "LongTermDebtCurrent", "DebtCurrent",
        "OtherShortTermBorrowings"]},
    "commercial_paper": {"kind": "instant", "tags": ["CommercialPaper"]},
    "long_term_debt": {"kind": "instant", "tags": [
        "LongTermDebtNoncurrent", "LongTermDebt"]},
    "total_liabilities": {"kind": "instant", "tags": ["Liabilities"]},
    "shareholders_equity": {"kind": "instant", "tags": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]},
    "noncontrolling_interest": {"kind": "instant", "tags": [
        "MinorityInterest"]},
    # mezzanine equity: temporary equity and redeemable NCI are separate,
    # ADDITIVE concepts (UBER pre-IPO carries both at once)
    "temporary_equity": {"kind": "instant", "tags": [
        "TemporaryEquityCarryingAmountAttributableToParent",
        "TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests",
        "TemporaryEquityCarryingAmount"]},
    "redeemable_noncontrolling_interest": {"kind": "instant", "tags": [
        "RedeemableNoncontrollingInterestEquityCarryingAmount",
        "RedeemableNoncontrollingInterestEquityCommonCarryingAmount",
        "RedeemableNoncontrollingInterestEquityCommonFairValue"]},
    # ---- cash flow (flow, USD) ----
    "operating_cash_flow": {"kind": "flow", "tags": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]},
    "capital_expenditures": {"kind": "flow", "tags": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets"]},
    "acquisitions": {"kind": "flow", "tags": [
        "PaymentsToAcquireBusinessesNetOfCashAcquired"]},
    "share_repurchases": {"kind": "flow", "tags": [
        "PaymentsForRepurchaseOfCommonStock"]},
    "dividends_paid": {"kind": "flow", "tags": [
        "PaymentsOfDividends", "PaymentsOfDividendsCommonStock"]},
    "stock_based_compensation": {"kind": "flow", "tags": [
        "ShareBasedCompensation"]},
    "debt_issuance": {"kind": "flow", "tags": [
        "ProceedsFromIssuanceOfLongTermDebt"]},
    "debt_repayment": {"kind": "flow", "tags": [
        "RepaymentsOfLongTermDebt"]},
    # ---- bank / consumer-finance fields (adapter v1) ----
    "net_interest_income": {"kind": "flow", "tags": [
        "InterestIncomeExpenseNet",
        "InterestIncomeExpenseAfterProvisionForLoanLoss"]},
    "noninterest_income": {"kind": "flow", "tags": ["NoninterestIncome"]},
    "noninterest_expense": {"kind": "flow", "tags": ["NoninterestExpense"]},
    "provision_for_credit_losses": {"kind": "flow", "tags": [
        "ProvisionForCreditLossExpenseReversal",
        "FinancingReceivableExcludingAccruedInterestCreditLossExpenseReversal",
        "ProvisionForLoanLossesExpensed",
        "ProvisionForLoanLeaseAndOtherLosses"]},
    "total_deposits": {"kind": "instant", "tags": ["Deposits"]},
}

INCOME_FIELDS = ["revenue", "cost_of_revenue", "gross_profit",
                 "research_and_development", "selling_general_and_administrative",
                 "operating_income", "interest_expense", "pretax_income",
                 "income_tax", "net_income", "basic_eps", "diluted_eps",
                 "weighted_average_basic_shares", "weighted_average_diluted_shares"]
BALANCE_FIELDS = [f for f, m in FIELD_MAP.items() if m["kind"] == "instant"]
CASHFLOW_FIELDS = ["operating_cash_flow", "capital_expenditures", "acquisitions",
                   "share_repurchases", "dividends_paid",
                   "stock_based_compensation", "debt_issuance", "debt_repayment"]
FLOW_FIELDS = [f for f, m in FIELD_MAP.items()
               if m["kind"] in ("flow", "per_share", "shares")]

# Q4-derivation by subtraction is invalid for per-share and share-count fields
# (they don't sum across quarters).
NON_ADDITIVE_FIELDS = {"basic_eps", "diluted_eps",
                       "weighted_average_basic_shares",
                       "weighted_average_diluted_shares"}

_UNIT_PREFERENCE = {
    "flow": ["USD"],
    "instant": ["USD"],
    "per_share": ["USD/shares"],
    "shares": ["shares"],
}


def units_for(field: str) -> list[str]:
    return _UNIT_PREFERENCE[FIELD_MAP[field]["kind"]]
