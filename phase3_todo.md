# To-do -- Phase 3
Work in local python 3.12 and uv. R2 actions in .env. Gate before every commit. 

Gate commands:
```uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy && uv run pytest```

## Order
0. Baseline -- branch off main, run the gate, confirm it passes. Add library, run gate again, confirm it passes. 

1. Explore -- use notebooks/. 
- Check ```sample_weight```, confirm sum by month is roughly equal to the month's trip count
- Check the wait distribution, and the negative-wait share of about 1.2%
- Check ```driver_pay```: units, zeros, outliers, tips? 
- Look at the January 2025 schema change (hvhfv-2025-cbd). make sure no feature carries the new fee column or anything else that exists only after the change
- Decide which features are known at request time. Wait-time features must be pickup zone, hour, day of week, base. 

2. ```sql/models/features.sql``` + ```models/data.py```. Put features and targets in SQl with ${placeholders}, and the loading, exclusions, and time-ordered split in Python. Settings go in config.py. 
- Test on the synthetic months from tests/conftest.py. 
- The split be by month, not random rows. 
- The same inputs must give the same frame every time. 
- Excluded rows must be counted and report, not silently dropped. 

3. ```models/train.py```. Baseline first, e.g. zone x hour weighted mean, then the model. 
- Use ```sample_weight``` in both fitting and metrics. 
- model must beat baseline on the holdout. 
- training on real data in ```analysis.yml``` should take under the 60-min job timeout. CI tests must use tiny data. 

4. ```models/score.py``` (drift). compute error on the new month against the holdout error, PSI per features, and residual shift by zone and hour. 
- pre-2025 months should mostly show low drift
- january 2025 should flag. if it doesn't, the monitor is too blunt to be useful. 
- add a unit test with a delibrately shifted synthetic month, and one with an unshifted month that must not flag. 

5. CLI and bundle. Write ```models``` section into estimates.json. Keep self-contained. 

6. Report, site, release. 
- drift section in ```report/build.py``` and ```site.py```. 
- change ```release.py``` so that "na" no longer passes the drift field. 
- don't change any field labels in ```.github/issue_template/review.yml```;  ```test_review_form.py``` checks that they match the record headings
- check the rendered markdown and the site page by eye on a phone width screen