import argparse
import csv
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from backtest import (
    infer_pair_from_csv_path,
    load_price_bars,
    run_backtest,
    run_multi_asset_backtest,
    write_timeseries_records_csv,
)
from portfolio import build_portfolio_config_from_env
from strategy import build_strategy_from_env


ROOT_DIR = Path(__file__).resolve().parent
COMPARE_OUTPUT_DIR = ROOT_DIR / os.getenv("ROOSTOO_COMPARE_OUTPUT_DIR", "artifacts/compare")
COMPARE_SUMMARY_DIR = COMPARE_OUTPUT_DIR / "summary"
COMPARE_TIMESERIES_DIR = COMPARE_OUTPUT_DIR / "timeseries"
COMPARE_RUNS_DIR = COMPARE_OUTPUT_DIR / "runs"
TMP_COMBINED_DIR = COMPARE_OUTPUT_DIR / "_tmp_combined"

COMPARE_LATEST_FILE = COMPARE_SUMMARY_DIR / "strategy_compare_latest.json"
COMPARE_HISTORY_JSONL_FILE = COMPARE_SUMMARY_DIR / "strategy_compare_runs.jsonl"
COMPARE_HISTORY_CSV_FILE = COMPARE_SUMMARY_DIR / "strategy_compare_runs.csv"

COMPARE_HISTORY_CSV_FIELDS = [
    "run_timestamp_utc",
    "run_timestamp_label",
    "preset",
    "csv_inputs",
    "output_json_path",
    "output_csv_path",
    "timestamped_output_json_path",
    "timestamped_output_csv_path",
    "timeseries_output_dir",
    "timestamped_timeseries_output_dir",
    "results",
]


def load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            os.environ[key] = value


def _serialize_csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return value


def _run_timestamp_label(run_timestamp: datetime) -> str:
    return run_timestamp.astimezone(timezone.utc).strftime("%Y%m%d_%H%M%S_UTC")


def _persist_compare_log(record: Dict[str, Any]) -> None:
    COMPARE_SUMMARY_DIR.mkdir(parents=True, exist_ok=True)

    with COMPARE_LATEST_FILE.open("w", encoding="utf-8") as file_handle:
        json.dump(record, file_handle, indent=2, sort_keys=True, default=str)

    with COMPARE_HISTORY_JSONL_FILE.open("a", encoding="utf-8") as file_handle:
        file_handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")

    csv_exists = COMPARE_HISTORY_CSV_FILE.exists()
    with COMPARE_HISTORY_CSV_FILE.open("a", encoding="utf-8", newline="") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=COMPARE_HISTORY_CSV_FIELDS)
        if not csv_exists:
            writer.writeheader()
        writer.writerow({field: _serialize_csv_value(record.get(field)) for field in COMPARE_HISTORY_CSV_FIELDS})


def combine_csvs_by_pair(csv_paths: List[str]) -> List[str]:
    grouped: Dict[str, List[Path]] = {}
    for csv_path in csv_paths:
        pair = infer_pair_from_csv_path(csv_path)
        grouped.setdefault(pair, []).append(Path(csv_path))

    combined_paths: List[str] = []
    TMP_COMBINED_DIR.mkdir(parents=True, exist_ok=True)

    for pair, paths in sorted(grouped.items()):
        if len(paths) == 1:
            combined_paths.append(str(paths[0]))
            continue

        paths = sorted(paths)
        first_stem_symbol = paths[0].stem.split("-", 1)[0].upper()
        output_path = TMP_COMBINED_DIR / f"{first_stem_symbol}-combined.csv"
        with output_path.open("w", encoding="utf-8", newline="") as destination:
            first = True
            for path in paths:
                with path.open("r", encoding="utf-8", newline="") as source:
                    for line in source:
                        if first or line.strip():
                            destination.write(line)
                first = False
        combined_paths.append(str(output_path))

    return combined_paths


def run_case(csv_paths: List[str], overrides: Dict[str, str], label: str, timeseries_csv_path: Path) -> Dict[str, Any]:
    previous_values = {key: os.environ.get(key) for key in overrides}
    try:
        for key, value in overrides.items():
            os.environ[key] = value

        strategy = build_strategy_from_env()
        portfolio_config = build_portfolio_config_from_env()
        starting_cash = float(os.getenv("ROOSTOO_BACKTEST_INITIAL_CASH", "10000"))
        fee_rate = float(os.getenv("ROOSTOO_BACKTEST_FEE_RATE", "0.001"))

        if len(csv_paths) == 1:
            bars = load_price_bars(csv_paths[0])
            summary = run_backtest(
                bars,
                pair=infer_pair_from_csv_path(csv_paths[0]),
                starting_cash=starting_cash,
                fee_rate=fee_rate,
                strategy=strategy,
                portfolio_config=portfolio_config,
                include_trade_records=False,
                include_timeseries_records=True,
            )
        else:
            summary = run_multi_asset_backtest(
                csv_paths,
                starting_cash=starting_cash,
                fee_rate=fee_rate,
                strategy=strategy,
                portfolio_config=portfolio_config,
                include_trade_records=False,
                include_timeseries_records=True,
            )

        timeseries_records = summary.pop("timeseries_records", None)
        if timeseries_records is not None:
            write_timeseries_records_csv(timeseries_records, str(timeseries_csv_path))

        return {
            "label": label,
            "strategy": summary["strategy"],
            "ending_equity": summary["ending_equity"],
            "total_return_pct": summary["total_return_pct"],
            "max_drawdown_pct": summary["max_drawdown_pct"],
            "fees_paid": summary["fees_paid"],
            "completed_trades": summary["completed_trades"],
            "win_rate_pct": summary["win_rate_pct"],
            "realized_pnl": summary["realized_pnl"],
            "pair_summaries": summary.get("pair_summaries"),
            "exit_reasons": summary.get("exit_reasons"),
            "timeseries_csv_path": str(timeseries_csv_path),
            "timeseries_records_count": len(timeseries_records or []),
            "overrides": overrides,
        }
    finally:
        for key, value in previous_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def build_v2_cases() -> List[tuple[str, Dict[str, str]]]:
    return [
        (
            "tuned_mean_reversion_filtered",
            {
                "ROOSTOO_STRATEGY": "mean_reversion",
                "ROOSTOO_PORTFOLIO_MAX_RANKED_BUYS_PER_BAR": "0",
            },
        ),
        (
            "regime_switch_v2",
            {
                "ROOSTOO_STRATEGY": "regime_switch",
                "ROOSTOO_PORTFOLIO_MAX_RANKED_BUYS_PER_BAR": "0",
            },
        ),
        (
            "regime_switch_v2_top1",
            {
                "ROOSTOO_STRATEGY": "regime_switch",
                "ROOSTOO_PORTFOLIO_MAX_RANKED_BUYS_PER_BAR": "1",
            },
        ),
    ]


def build_mr_competition_cases() -> List[tuple[str, Dict[str, str]]]:
    return [
        (
            "mr_baseline_sol3",
            {
                "ROOSTOO_STRATEGY": "mean_reversion",
                "ROOSTOO_PORTFOLIO_MAX_RANKED_BUYS_PER_BAR": "0",
            },
        ),
        (
            "mr_tighter_vol_filter_sol3",
            {
                "ROOSTOO_STRATEGY": "mean_reversion",
                "ROOSTOO_MR_VOLATILITY_PERIOD": "20",
                "ROOSTOO_MR_MAX_VOLATILITY": "0.014",
                "ROOSTOO_PORTFOLIO_MAX_RANKED_BUYS_PER_BAR": "0",
            },
        ),
        (
            "mr_tighter_vol_filter_top1_sol3",
            {
                "ROOSTOO_STRATEGY": "mean_reversion",
                "ROOSTOO_MR_VOLATILITY_PERIOD": "20",
                "ROOSTOO_MR_MAX_VOLATILITY": "0.014",
                "ROOSTOO_PORTFOLIO_MAX_RANKED_BUYS_PER_BAR": "1",
            },
        ),
        (
            "mr_tighter_vol_filter_top2_sol3",
            {
                "ROOSTOO_STRATEGY": "mean_reversion",
                "ROOSTOO_MR_VOLATILITY_PERIOD": "20",
                "ROOSTOO_MR_MAX_VOLATILITY": "0.014",
                "ROOSTOO_PORTFOLIO_MAX_RANKED_BUYS_PER_BAR": "2",
            },
        ),
    ]


def build_mr_positive_push_cases() -> List[tuple[str, Dict[str, str]]]:
    return [
        (
            "mr_top1_baseline_sol3",
            {
                "ROOSTOO_STRATEGY": "mean_reversion",
                "ROOSTOO_PORTFOLIO_MAX_RANKED_BUYS_PER_BAR": "1",
            },
        ),
        (
            "mr_top1_vol013_sol3",
            {
                "ROOSTOO_STRATEGY": "mean_reversion",
                "ROOSTOO_MR_MAX_VOLATILITY": "0.013",
                "ROOSTOO_PORTFOLIO_MAX_RANKED_BUYS_PER_BAR": "1",
            },
        ),
        (
            "mr_top1_vol012_sol3",
            {
                "ROOSTOO_STRATEGY": "mean_reversion",
                "ROOSTOO_MR_MAX_VOLATILITY": "0.012",
                "ROOSTOO_PORTFOLIO_MAX_RANKED_BUYS_PER_BAR": "1",
            },
        ),
        (
            "mr_top1_vol013_mid009_sol3",
            {
                "ROOSTOO_STRATEGY": "mean_reversion",
                "ROOSTOO_MR_MAX_VOLATILITY": "0.013",
                "ROOSTOO_MR_MIN_DISTANCE_TO_MID_PCT": "0.009",
                "ROOSTOO_PORTFOLIO_MAX_RANKED_BUYS_PER_BAR": "1",
            },
        ),
        (
            "mr_top1_vol012_mid009_sol3",
            {
                "ROOSTOO_STRATEGY": "mean_reversion",
                "ROOSTOO_MR_MAX_VOLATILITY": "0.012",
                "ROOSTOO_MR_MIN_DISTANCE_TO_MID_PCT": "0.009",
                "ROOSTOO_PORTFOLIO_MAX_RANKED_BUYS_PER_BAR": "1",
            },
        ),
        (
            "mr_top1_vol013_mid009_sell100_sol3",
            {
                "ROOSTOO_STRATEGY": "mean_reversion",
                "ROOSTOO_MR_MAX_VOLATILITY": "0.013",
                "ROOSTOO_MR_MIN_DISTANCE_TO_MID_PCT": "0.009",
                "ROOSTOO_PORTFOLIO_MAX_RANKED_BUYS_PER_BAR": "1",
                "ROOSTOO_PORTFOLIO_SELL_FRACTION_PCT": "1.0",
            },
        ),
        (
            "mr_top1_vol012_mid009_sell100_sol3",
            {
                "ROOSTOO_STRATEGY": "mean_reversion",
                "ROOSTOO_MR_MAX_VOLATILITY": "0.012",
                "ROOSTOO_MR_MIN_DISTANCE_TO_MID_PCT": "0.009",
                "ROOSTOO_PORTFOLIO_MAX_RANKED_BUYS_PER_BAR": "1",
                "ROOSTOO_PORTFOLIO_SELL_FRACTION_PCT": "1.0",
            },
        ),
    ]


def build_mr_trend_extension_cases() -> List[tuple[str, Dict[str, str]]]:
    return [
        (
            "mr_candidate_baseline",
            {
                "ROOSTOO_STRATEGY": "mean_reversion",
            },
        ),
        (
            "mr_candidate_trend_extension",
            {
                "ROOSTOO_STRATEGY": "mean_reversion",
                "ROOSTOO_MR_TREND_EXTENSION_ENABLED": "true",
            },
        ),
        (
            "mr_candidate_trend_extension_entry_filter",
            {
                "ROOSTOO_STRATEGY": "mean_reversion",
                "ROOSTOO_MR_TREND_EXTENSION_ENABLED": "true",
                "ROOSTOO_MR_REQUIRE_PRICE_ABOVE_TREND_EMA_FOR_ENTRY": "true",
                "ROOSTOO_MR_ENTRY_BELOW_TREND_EMA_BUFFER_PCT": "0.005",
            },
        ),
    ]


def build_mtf_v2_cases() -> List[tuple[str, Dict[str, str]]]:
    return [
        (
            "mean_reversion_current_baseline",
            {
                "ROOSTOO_STRATEGY": "mean_reversion",
            },
        ),
        (
            "mtf_mean_reversion_v2_experimental",
            {
                "ROOSTOO_STRATEGY": "mtf_mean_reversion_v2",
            },
        ),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare a preset set of strategies on one backtest window.")
    parser.add_argument(
        "--dotenv",
        default=".env",
        help="Env file to load before applying preset overrides. Defaults to .env",
    )
    parser.add_argument(
        "--backtest",
        metavar="CSV_PATH",
        nargs="+",
        required=True,
        help="One or more CSV files. Duplicate pairs are combined automatically in timestamp order.",
    )
    parser.add_argument(
        "--preset",
        default="v2",
        choices=["v2", "mr_competition", "mr_positive_push", "mr_trend_extension", "mtf_v2"],
        help="Comparison preset to run. Default: v2",
    )
    parser.add_argument(
        "--output-json",
        help="Optional explicit output JSON path. Defaults to artifacts/compare/summary/latest_strategy_comparison_<preset>.json",
    )
    parser.add_argument(
        "--output-csv",
        help="Optional explicit output CSV path. Defaults to artifacts/compare/summary/latest_strategy_comparison_<preset>.csv",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_dotenv(ROOT_DIR / args.dotenv)
    run_timestamp = datetime.now(timezone.utc)
    timestamp_label = _run_timestamp_label(run_timestamp)

    combined_paths = combine_csvs_by_pair(args.backtest)
    if args.preset == "v2":
        cases = build_v2_cases()
    elif args.preset == "mr_competition":
        cases = build_mr_competition_cases()
    elif args.preset == "mr_positive_push":
        cases = build_mr_positive_push_cases()
    elif args.preset == "mr_trend_extension":
        cases = build_mr_trend_extension_cases()
    elif args.preset == "mtf_v2":
        cases = build_mtf_v2_cases()
    else:
        raise ValueError(f"Unsupported preset: {args.preset}")
    COMPARE_SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    COMPARE_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    output_json = (
        Path(args.output_json)
        if args.output_json
        else COMPARE_SUMMARY_DIR / f"latest_strategy_comparison_{args.preset}.json"
    )
    output_csv = (
        Path(args.output_csv)
        if args.output_csv
        else COMPARE_SUMMARY_DIR / f"latest_strategy_comparison_{args.preset}.csv"
    )
    timeseries_output_dir = COMPARE_TIMESERIES_DIR / args.preset
    timeseries_output_dir.mkdir(parents=True, exist_ok=True)
    timestamped_timeseries_output_dir = COMPARE_RUNS_DIR / f"{args.preset}_{timestamp_label}"
    timestamped_timeseries_output_dir.mkdir(parents=True, exist_ok=True)

    results = [
        run_case(
            combined_paths,
            overrides,
            label,
            timestamped_timeseries_output_dir / f"{label}.csv",
        )
        for label, overrides in cases
    ]
    output_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
    with output_csv.open("w", encoding="utf-8", newline="") as file_handle:
        writer = csv.DictWriter(
            file_handle,
            fieldnames=[
                "label",
                "strategy",
                "ending_equity",
                "total_return_pct",
                "max_drawdown_pct",
                "fees_paid",
                "completed_trades",
                "win_rate_pct",
                "realized_pnl",
                "pair_summaries",
                "exit_reasons",
                "timeseries_csv_path",
                "latest_timeseries_csv_path",
                "timeseries_records_count",
                "overrides",
            ],
        )
        writer.writeheader()
        for row in results:
            writer.writerow({key: _serialize_csv_value(row.get(key)) for key in writer.fieldnames})

    timestamped_output_json = COMPARE_RUNS_DIR / f"strategy_comparison_{args.preset}_{timestamp_label}.json"
    timestamped_output_csv = COMPARE_RUNS_DIR / f"strategy_comparison_{args.preset}_{timestamp_label}.csv"
    shutil.copy2(output_json, timestamped_output_json)
    shutil.copy2(output_csv, timestamped_output_csv)

    latest_timeseries_output_dir = timeseries_output_dir / "latest"
    latest_timeseries_output_dir.mkdir(parents=True, exist_ok=True)
    for row in results:
        source_path = Path(row["timeseries_csv_path"])
        latest_target = latest_timeseries_output_dir / source_path.name
        shutil.copy2(source_path, latest_target)
        row["latest_timeseries_csv_path"] = str(latest_target)

    record = {
        "run_timestamp_utc": run_timestamp.isoformat(),
        "run_timestamp_label": timestamp_label,
        "preset": args.preset,
        "csv_inputs": args.backtest,
        "combined_csv_inputs": combined_paths,
        "output_json_path": str(output_json),
        "output_csv_path": str(output_csv),
        "timestamped_output_json_path": str(timestamped_output_json),
        "timestamped_output_csv_path": str(timestamped_output_csv),
        "timeseries_output_dir": str(latest_timeseries_output_dir),
        "timestamped_timeseries_output_dir": str(timestamped_timeseries_output_dir),
        "results": results,
    }
    _persist_compare_log(record)

    print(
        json.dumps(
            {
                **record,
                "compare_latest_path": str(COMPARE_LATEST_FILE),
                "compare_history_jsonl_path": str(COMPARE_HISTORY_JSONL_FILE),
                "compare_history_csv_path": str(COMPARE_HISTORY_CSV_FILE),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
