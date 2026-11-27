# Poland bankruptcy data

Each row contains 64 financial attributes (`Attr1` through `Attr64`) and a target named `class`. A target of `1` means the company went bankrupt during the indicated prediction horizon. A `?` is a missing input.

| Prediction horizon | CSV | Original file | Rows | Bankruptcy cases |
| --- | --- | --- | ---: | ---: |
| 1 year | [horizon-1y.csv](horizon-1y.csv) | `5year.arff` | 5,910 | 410 |
| 2 years | [horizon-2y.csv](horizon-2y.csv) | `4year.arff` | 9,792 | 515 |
| 3 years | [horizon-3y.csv](horizon-3y.csv) | `3year.arff` | 10,503 | 495 |
| 4 years | [horizon-4y.csv](horizon-4y.csv) | `2year.arff` | 10,173 | 400 |
| 5 years | [horizon-5y.csv](horizon-5y.csv) | `1year.arff` | 7,027 | 271 |

Start with the one-year file for a one-year prediction experiment. Treat each horizon as a separate prediction target. The UCI page lists the original definitions of all 64 attributes.

Source and attribution: Tomczak, S. (2016), *Polish Companies Bankruptcy*, UCI Machine Learning Repository. Converted from ARFF to CSV and renamed by prediction horizon. [Dataset and attribute definitions](https://doi.org/10.24432/C5F600) · [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
