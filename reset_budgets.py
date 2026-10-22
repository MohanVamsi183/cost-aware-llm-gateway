"""
Show (and optionally reset) this month's spend for every tenant in DynamoDB.

    python reset_budgets.py          # just show spend
    python reset_budgets.py --reset  # set every tenant back to $0 for this month

Handy before a fresh traffic run or before recording the demo.
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from app.budgets import DynamoBudgets, current_period  # noqa: E402

TABLE = os.getenv("BUDGET_TABLE", "mohan-gateway-budgets")
budgets = DynamoBudgets(TABLE, "us-east-1")
tenants = json.loads(Path("tenants.json").read_text())
period = current_period()

if "--reset" in sys.argv:
    for t in tenants:
        budgets.table.delete_item(Key={"tenant_id": t["tenant_id"], "period": period})
    print(f"Reset all tenants to $0 for {period}.")

print(f"Spend for {period} (table {TABLE}):")
for t in tenants:
    spent = budgets.get_spent(t["tenant_id"])
    pct = spent / t["monthly_budget_usd"] * 100
    print(f"  {t['tenant_id']:18} ${spent:.6f} of ${t['monthly_budget_usd']:.3f}  ({pct:.0f}%)")
