# Notes for coding agents

- Read `README.md` and `METHODS.md` first. The project brief is the source of scope; the
  owner (an economist) makes every call marked `OWNER DECISION`. Do not resolve those.
- Stack is fixed: DuckDB as a library, Cloudflare R2 via S3 API, GitHub Actions, Cloudflare
  Pages. No other cloud services, no servers, no feature stores.
- Raw TLC data is never stored or committed. Only derived outputs under `data/derived/`
  (mirrored to R2). Keep total R2 footprint under 2 GB.
- All SQL lives in `sql/` as `.sql` files rendered with `${placeholders}`; keep it readable.
- All paths, dates, zones and thresholds come from `src/roundtrip/config.py`.
- Checks: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy && uv run pytest`.
  CI must stay under 10 minutes; heavy work belongs in `monthly.yml` / `backfill.yml`.
- Network from the local shell may be blocked; real-data runs go through GitHub Actions:
  push `trial/<start>_<end>` to run the backfill, `analysis/<name>` to run the analyses.
- Phase 2 analyses live in `src/roundtrip/causal/` and write one `estimates.json` bundle;
  `report/build.py` turns it into `REPORTS/congestion_pricing.md`; `release.py` is the
  review gate. Keep the bundle self-contained: the report and the dashboard read nothing
  else.
