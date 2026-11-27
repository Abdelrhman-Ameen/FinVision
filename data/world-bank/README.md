# World Bank annual indicators

These CSVs contain a World Development Indicators snapshot retrieved on 8 October 2026. Coverage varies by indicator and economy. Empty values represent unavailable observations.

| CSV | How to read it |
| --- | --- |
| [Egypt, long](egypt_macro_annual_long.csv) | One row per year and indicator. Includes source URLs and retrieval dates. |
| [Egypt, wide](egypt_macro_annual_wide.csv) | One row per year. Indicator codes are column names. |
| [Regional and emerging economies, long](mena_emerging_macro_annual_long.csv) | One row per economy, year and indicator. Contains inflation, official exchange rates, real GDP growth and lending interest rates. |

Source and attribution: The World Bank, *World Development Indicators*, via the World Bank Indicators API. The files select economies and series from WDI and reshape them into long or wide tables. [WDI catalog](https://datacatalog.worldbank.org/infrastructure-data/search/dataset/0037712/world-development-indicators) · [Dataset terms](https://www.worldbank.org/en/about/legal/terms-of-use-for-datasets)
