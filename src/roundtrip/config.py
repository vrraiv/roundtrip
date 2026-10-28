"""Project configuration.

Every path, date, zone definition and threshold lives here or in the environment.
Nothing downstream hard-codes them. Values can be overridden with ``ROUNDTRIP_*``
environment variables or a git-ignored ``.env`` file; R2 credentials use the bare
``R2_*`` names required by the GitHub Actions secrets.
"""

from __future__ import annotations

import datetime as dt
import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def validate_month(value: str) -> str:
    """Return ``value`` if it is a ``YYYY-MM`` string, else raise ``ValueError``."""
    if not MONTH_RE.match(value):
        msg = f"month must be YYYY-MM, got {value!r}"
        raise ValueError(msg)
    return value


# Taxi zones inside the Manhattan Central Business District (Congestion Relief Zone),
# taken from the MTA "Central Business District Taxi Zones" dataset (data.ny.gov yfdc-w5jh).
# 38 zones. Boundary zones that straddle 60th St (e.g. 163 Midtown North) are included by
# the MTA; zones north of the line (140/141 Lenox Hill, 142/143 Lincoln Square, 236/237
# Upper East Side) are not. OWNER DECISION: confirm this list before phase 2 estimation.
CBD_ZONES: tuple[int, ...] = (
    4, 12, 13, 45, 48, 50, 68, 79, 87, 88, 90, 100, 107, 113, 114, 125, 137, 144, 148,
    158, 161, 162, 163, 164, 170, 186, 209, 211, 224, 229, 230, 231, 232, 233, 234, 246,
    249, 261,
)  # fmt: skip

# Airport zones (Newark 1, JFK 132, LaGuardia 138). Whether to drop airport trips from the
# causal panel is an owner decision; the panel keeps them and flags them.
AIRPORT_ZONES: tuple[int, ...] = (1, 132, 138)

# Control-group zone lists for the event study (brief section 6a) and the synthetic-control
# donor pool (owner, 2026-10-27). Zone ids and names are from the TLC taxi zone lookup.
#
# "Manhattan north of 60th" is taken as every Manhattan zone outside the CBD except parks and
# islands with negligible pickups (computed from the lookup at run time, minus this list).
MANHATTAN_EXCLUDED_ZONES: tuple[int, ...] = (
    43,  # Central Park
    103,
    104,
    105,  # Governor's / Ellis / Liberty Island
    120,  # Highbridge Park
    128,  # Inwood Hill Park
    194,  # Randalls Island
)
# Inner Brooklyn and Queens zones along the East River, facing the CBD.
INNER_BK_QUEENS_ZONES: tuple[int, ...] = (
    # Brooklyn
    25,  # Boerum Hill
    33,  # Brooklyn Heights
    34,  # Brooklyn Navy Yard
    40,  # Carroll Gardens
    49,  # Clinton Hill
    52,  # Cobble Hill
    54,  # Columbia Street
    65,  # Downtown Brooklyn/MetroTech
    66,  # DUMBO/Vinegar Hill
    80,  # East Williamsburg
    97,  # Fort Greene
    106,  # Gowanus
    112,  # Greenpoint
    181,  # Park Slope
    189,  # Prospect Heights
    195,  # Red Hook
    217,  # South Williamsburg
    255,  # Williamsburg (North Side)
    256,  # Williamsburg (South Side)
    # Queens
    7,  # Astoria
    145,  # Long Island City/Hunters Point
    146,  # Long Island City/Queens Plaza
    179,  # Old Astoria
    193,  # Queensbridge/Ravenswood
    223,  # Steinway
    226,  # Sunnyside
    260,  # Woodside
)

# HVFHS licence numbers from the TLC data dictionary (March 2025 edition).
BASE_CODES: dict[str, str] = {
    "HV0002": "Juno",
    "HV0003": "Uber",
    "HV0004": "Via",
    "HV0005": "Lyft",
}


class R2Settings(BaseSettings):
    """Cloudflare R2 credentials. All optional locally; required in the monthly job."""

    model_config = SettingsConfigDict(env_prefix="R2_", env_file=".env", extra="ignore")

    account_id: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    bucket: str | None = None

    @field_validator("account_id")
    @classmethod
    def _bare_account_id(cls, value: str | None) -> str | None:
        """Accept the S3 endpoint URL in place of the bare account id.

        The Cloudflare dashboard shows ``https://<id>.r2.cloudflarestorage.com`` next to the
        id, and pasting the whole URL into the secret is an easy mistake.
        """
        if value is None:
            return None
        v = value.strip()
        v = v.removeprefix("https://").removeprefix("http://")
        v = v.split("/", 1)[0].split(".", 1)[0]
        return v or None

    @property
    def configured(self) -> bool:
        return all((self.account_id, self.access_key_id, self.secret_access_key, self.bucket))

    @property
    def endpoint_url(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"


class ValidationThresholds(BaseModel):
    """Thresholds for the validation suite (section 5 of the brief)."""

    row_count_tolerance: float = 0.30  # +/- vs trailing-12-month median
    row_count_min_history: int = 3  # months of history before the row-count check is hard
    null_rate_band_margin: float = 0.05  # absolute widening of historical null-rate bands
    null_rate_min_history: int = 3
    max_wait_seconds: int = 3 * 3600
    max_trip_hours: int = 6
    timestamp_slack_days: int = 1  # trips may spill one day past the file's month
    fare_p999_min: float = 100.0
    fare_p999_max: float = 600.0
    pay_p999_min: float = 80.0
    pay_p999_max: float = 500.0
    max_flagged_share: float = 0.02  # hard fail if more than this share of trips is flagged
    # Pickup-before-request is a steady ~1.2% of HVFHV trips (reporting quirk; excluded from
    # wait-time means). Owner decision 2026-10-27: keep the check hard, limit 5%.
    max_pickup_before_request_share: float = 0.05
    max_unknown_zone_share: float = 0.001
    max_unknown_base_share: float = 0.001


class Settings(BaseSettings):
    """Top-level settings. Instantiate via :func:`get_settings`."""

    model_config = SettingsConfigDict(
        env_prefix="ROUNDTRIP_", env_file=".env", env_nested_delimiter="__", extra="ignore"
    )

    # --- paths -------------------------------------------------------------------------
    data_dir: Path = Path("data")
    sql_dir: Path = Path(__file__).resolve().parent.parent.parent / "sql"

    # --- data source --------------------------------------------------------------------
    # TLC's CloudFront host. A ``file://`` URL pointing at a directory with the same layout
    # (``trip-data/fhvhv_tripdata_YYYY-MM.parquet``) is accepted for tests and offline runs.
    tlc_base_url: str = "https://d37ci6vzurychx.cloudfront.net"
    dataset: str = "fhvhv"
    start_month: str = "2023-01"  # confirmed by the owner 2026-10-27
    end_month: str | None = None  # None = discover up to the current month

    # --- study design -------------------------------------------------------------------
    treatment_date: dt.date = dt.date(2025, 1, 5)
    # Anticipation window (owner design, 2026-10-27): from the 14 Nov 2024 announcement of
    # the $9 toll to the 5 Jan 2025 start. Handled with a synthetic-control forecast of CBD
    # outcomes over the window; the same calendar window one year earlier, when nothing
    # happened, is the placebo used to test the forecast design.
    anticipation_start: dt.date = dt.date(2024, 11, 14)
    anticipation_end: dt.date = dt.date(2025, 1, 5)  # exclusive; equals treatment_date
    placebo_window_start: dt.date = dt.date(2023, 11, 14)
    placebo_window_end: dt.date = dt.date(2024, 1, 5)  # exclusive
    # Clean pre-event window for the before/after comparison (owner, 2026-10-27).
    pre_window_start: dt.date = dt.date(2024, 6, 1)
    # Synthetic-control design (owner, 2026-10-27): daily units; outcomes trip counts, mean
    # wait, mean base fare; donors = non-CBD Manhattan plus inner Brooklyn/Queens zones on
    # the East River, airports excluded; simplex weights fitted before the announcement.
    synth_outcomes: tuple[str, ...] = ("n_trips", "mean_wait_seconds", "mean_base_fare")
    # Main event study reports three comparisons (owner, 2026-10-27): the same weeks one year
    # earlier, the synthetic control, and the pre-event window above.
    comparison_designs: tuple[str, ...] = ("prior_year", "synthetic_control", "pre_window")
    # OWNER DECISION still open: whether to drop airport trips. Default keeps them.
    exclude_airport_trips: bool = False
    cbd_zones: tuple[int, ...] = CBD_ZONES
    airport_zones: tuple[int, ...] = AIRPORT_ZONES
    manhattan_excluded_zones: tuple[int, ...] = MANHATTAN_EXCLUDED_ZONES
    inner_bk_queens_zones: tuple[int, ...] = INNER_BK_QUEENS_ZONES
    base_codes: dict[str, str] = Field(default_factory=lambda: dict(BASE_CODES))
    # Event-study window in weeks relative to the treatment week; ends are binned.
    event_weeks_before: int = 30
    event_weeks_after: int = 30
    # Fake treatment dates for the placebo-in-time event studies (all before the
    # announcement, so the "post" periods are untreated).
    placebo_dates: tuple[dt.date, ...] = (
        dt.date(2024, 3, 3),
        dt.date(2024, 6, 2),
        dt.date(2024, 9, 1),
    )
    bootstrap_reps: int = 1000
    # The comparisons evaluate the first year after treatment, so the prior-year design
    # never compares a treated day with another treated day.
    post_window_days: int = 364
    # Out-of-sample test of the synthetic control (owner review of the 2026-08 release):
    # train on Jan to 13 Nov 2023 only, forecast the untreated year that follows.
    oos_train_start: dt.date = dt.date(2023, 1, 1)
    oos_train_end: dt.date = dt.date(2023, 11, 13)
    oos_holdout_end: dt.date = dt.date(2024, 11, 13)
    # A model "passes" if its mean gap over the hold-out year, and over its last quarter,
    # is within this many percent (about the size of the 2023 test-window gaps).
    oos_drift_tolerance_pct: float = 3.0
    # Prior-year design run on an untreated window (Jan to 13 Nov 2024 vs 2023): it should
    # find nothing if the design is sound.
    prior_year_placebo_start: dt.date = dt.date(2024, 1, 5)
    bootstrap_seed: int = 20250105

    # --- compute ------------------------------------------------------------------------
    duckdb_memory_limit: str = "10GB"
    duckdb_threads: int = 4

    # --- sampling -----------------------------------------------------------------------
    sample_seed: int = 20230101
    sample_rows_per_stratum: int = 400  # strata = base x pickup borough x hour of day

    # --- validation ---------------------------------------------------------------------
    validation: ValidationThresholds = Field(default_factory=ValidationThresholds)

    # --- storage ------------------------------------------------------------------------
    r2: R2Settings = Field(default_factory=R2Settings)

    @field_validator("start_month", "end_month")
    @classmethod
    def _check_month(cls, value: str | None) -> str | None:
        return None if value is None else validate_month(value)

    # Derived paths. Everything under ``derived_dir`` mirrors the R2 bucket key-for-key.
    @property
    def derived_dir(self) -> Path:
        return self.data_dir / "derived"

    @property
    def raw_cache_dir(self) -> Path:
        """Transient download location. Never synced; deleted after processing in CI."""
        return self.data_dir / "raw_cache"

    @property
    def runs_dir(self) -> Path:
        return self.derived_dir / "runs"

    @property
    def manifest_path(self) -> Path:
        return self.derived_dir / "manifest.json"

    @property
    def duckdb_path(self) -> Path:
        return self.data_dir / "roundtrip.duckdb"

    @property
    def zone_lookup_path(self) -> Path:
        return self.derived_dir / "zones" / "taxi_zone_lookup.csv"

    @property
    def panel_glob(self) -> str:
        return str(self.derived_dir / "panel" / "zone_hour" / "month=*" / "data.parquet")

    @property
    def estimates_dir(self) -> Path:
        return self.derived_dir / "estimates"

    @property
    def reports_dir(self) -> Path:
        """Markdown reports and figures committed to the repo (``REPORTS/``)."""
        return self.repo_dir / "REPORTS"

    @property
    def reviews_dir(self) -> Path:
        return self.repo_dir / "reviews"

    @property
    def releases_dir(self) -> Path:
        return self.repo_dir / "releases"

    @property
    def repo_dir(self) -> Path:
        return self.sql_dir.parent

    def panel_path(self, month: str) -> Path:
        return self.derived_dir / "panel" / "zone_hour" / f"month={month}" / "data.parquet"

    def sample_path(self, month: str) -> Path:
        return self.derived_dir / "sample" / "trips" / f"month={month}" / "data.parquet"

    def validation_path(self, month: str, suffix: str = "json") -> Path:
        return self.derived_dir / "validation" / f"{month}.{suffix}"

    def raw_path(self, month: str) -> Path:
        return self.raw_cache_dir / f"{self.dataset}_tripdata_{month}.parquet"

    def raw_url(self, month: str) -> str:
        base = self.tlc_base_url.rstrip("/")
        return f"{base}/trip-data/{self.dataset}_tripdata_{month}.parquet"

    @property
    def zone_lookup_url(self) -> str:
        return f"{self.tlc_base_url.rstrip('/')}/misc/taxi_zone_lookup.csv"

    @property
    def zone_shapefile_url(self) -> str:
        return f"{self.tlc_base_url.rstrip('/')}/misc/taxi_zones.zip"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings instance. Call ``get_settings.cache_clear()`` in tests."""
    return Settings()


def month_range(start: str, end: str) -> list[str]:
    """Inclusive list of ``YYYY-MM`` strings from ``start`` to ``end``."""
    validate_month(start)
    validate_month(end)
    y, m = (int(x) for x in start.split("-"))
    ey, em = (int(x) for x in end.split("-"))
    out: list[str] = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def month_bounds(month: str) -> tuple[dt.date, dt.date]:
    """First day of ``month`` and first day of the following month."""
    validate_month(month)
    y, m = (int(x) for x in month.split("-"))
    start = dt.date(y, m, 1)
    end = dt.date(y + 1, 1, 1) if m == 12 else dt.date(y, m + 1, 1)
    return start, end


def current_month(today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    return f"{today.year:04d}-{today.month:02d}"
