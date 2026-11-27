import pandas as pd

# =========================
# 1. Input / Output
# =========================
from pathlib import Path


# Get the folder where this Python script is located
BASE_DIR = Path(__file__).resolve().parent

input_file = BASE_DIR / "1year.arff"
output_file = BASE_DIR / "polish_1year.csv"


# =========================
# 2. Feature names X1 -> X64
# =========================
feature_names = [
    "net_profit_total_assets",
    "total_liabilities_total_assets",
    "working_capital_total_assets",
    "current_assets_short_term_liabilities",
    "cash_securities_receivables_minus_short_term_liabilities_over_operating_expenses_minus_depreciation_times_365",
    "retained_earnings_total_assets",
    "ebit_total_assets",
    "book_value_equity_total_liabilities",
    "sales_total_assets",
    "equity_total_assets",
    "gross_profit_extraordinary_items_financial_expenses_total_assets",
    "gross_profit_short_term_liabilities",
    "gross_profit_plus_depreciation_sales",
    "gross_profit_plus_interest_total_assets",
    "total_liabilities_times_365_over_gross_profit_plus_depreciation",
    "gross_profit_plus_depreciation_total_liabilities",
    "total_assets_total_liabilities",
    "gross_profit_total_assets",
    "gross_profit_sales",
    "inventory_times_365_sales",
    "sales_current_over_sales_previous",
    "operating_profit_total_assets",
    "net_profit_sales",
    "gross_profit_3_years_total_assets",
    "equity_minus_share_capital_total_assets",
    "net_profit_plus_depreciation_total_liabilities",
    "operating_profit_financial_expenses",
    "working_capital_fixed_assets",
    "log_total_assets",
    "total_liabilities_minus_cash_sales",
    "gross_profit_plus_interest_sales",
    "current_liabilities_times_365_cost_of_products_sold",
    "operating_expenses_short_term_liabilities",
    "operating_expenses_total_liabilities",
    "profit_on_sales_total_assets",
    "total_sales_total_assets",
    "current_assets_minus_inventories_long_term_liabilities",
    "constant_capital_total_assets",
    "profit_on_sales_sales",
    "current_assets_minus_inventory_minus_receivables_short_term_liabilities",
    "total_liabilities_over_operating_profit_plus_depreciation_times_12_over_365",
    "operating_profit_sales",
    "receivables_plus_inventory_turnover_days",
    "receivables_times_365_sales",
    "net_profit_inventory",
    "current_assets_minus_inventory_short_term_liabilities",
    "inventory_times_365_cost_of_products_sold",
    "ebitda_total_assets",
    "ebitda_sales",
    "current_assets_total_liabilities",
    "short_term_liabilities_total_assets",
    "short_term_liabilities_times_365_cost_of_products_sold",
    "equity_fixed_assets",
    "constant_capital_fixed_assets",
    "working_capital",
    "sales_minus_cost_of_products_sold_sales",
    "current_assets_minus_inventory_minus_short_term_liabilities_over_sales_minus_gross_profit_minus_depreciation",
    "total_costs_total_sales",
    "long_term_liabilities_equity",
    "sales_inventory",
    "sales_receivables",
    "short_term_liabilities_times_365_sales",
    "sales_short_term_liabilities",
    "sales_fixed_assets",
]

columns = feature_names + ["class"]


# =========================
# 3. Read ARFF
# =========================
with open(input_file, "r", encoding="utf-8") as f:
    lines = f.readlines()


# Find @data
data_index = None

for i, line in enumerate(lines):
    if line.strip().lower() == "@data":
        data_index = i
        break

if data_index is None:
    raise ValueError("Could not find @data in the ARFF file.")


# =========================
# 4. Extract data rows
# =========================
data = []

for line in lines[data_index + 1:]:

    line = line.strip()

    # Skip empty lines and comments
    if not line or line.startswith("%"):
        continue

    values = [x.strip() for x in line.split(",")]

    if len(values) != 65:
        print(
            f"Warning: skipped row with {len(values)} values "
            f"instead of 65."
        )
        continue

    data.append(values)


# =========================
# 5. Create DataFrame
# =========================
df = pd.DataFrame(data, columns=columns)


# =========================
# 6. Convert values
# =========================

# ? = missing value
df = df.replace("?", pd.NA)

# Convert X1-X64 to numeric
for column in feature_names:
    df[column] = pd.to_numeric(
        df[column],
        errors="coerce"
    )

# Convert class to integer
df["class"] = pd.to_numeric(
    df["class"],
    errors="coerce"
).astype("Int64")


# =========================
# 7. Save CSV
# =========================
df.to_csv(
    output_file,
    index=False
)


# =========================
# 8. Print summary
# =========================
print("\n====================================")
print("ARFF → CSV conversion completed")
print("====================================")

print(f"Input file : {input_file}")
print(f"Output file: {output_file}")

print(f"\nRows    : {df.shape[0]}")
print(f"Columns : {df.shape[1]}")

print("\nClass distribution:")
print(df["class"].value_counts(dropna=False).sort_index())

print("\nMissing values:")
print(df.isna().sum().sum())

print("\nFirst 5 rows:")
print(df.head())