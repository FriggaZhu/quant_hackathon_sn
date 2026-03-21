import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


DEFAULT_OUTPUT_DIR = Path("artifacts/analysis")


def _load_trade_rows(csv_path: Path) -> List[Dict[str, str]]:
    with csv_path.open("r", encoding="utf-8", newline="") as file_handle:
        return list(csv.DictReader(file_handle))


def _parse_float(value: Any) -> Optional[float]:
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_strategy_debug(raw_value: str) -> Dict[str, Any]:
    if not raw_value:
        return {}
    try:
        parsed = json.loads(raw_value)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _bucket_label(value: Optional[float], cut_points: Iterable[float], precision: int = 4) -> str:
    if value is None:
        return "unknown"

    ordered = list(cut_points)
    previous = None
    for cut_point in ordered:
        if value < cut_point:
            if previous is None:
                return f"<{cut_point:.{precision}f}"
            return f"{previous:.{precision}f}-{cut_point:.{precision}f}"
        previous = cut_point

    return f">={ordered[-1]:.{precision}f}"


def _bucket_rsi(value: Optional[float]) -> str:
    if value is None:
        return "unknown"
    if value < 20:
        return "<20"
    if value < 25:
        return "20-25"
    if value < 30:
        return "25-30"
    if value < 35:
        return "30-35"
    if value < 40:
        return "35-40"
    return ">=40"


def _summarize_records(records: List[Dict[str, Any]], field_name: str) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for record in records:
        group_key = str(record.get(field_name, "unknown") or "unknown")
        current = grouped.setdefault(
            group_key,
            {
                field_name: group_key,
                "sell_events": 0,
                "wins": 0,
                "losses": 0,
                "realized_pnl": 0.0,
                "return_pct_total": 0.0,
                "fees_paid": 0.0,
            },
        )
        current["sell_events"] += 1
        realized_pnl = float(record.get("realized_pnl", 0.0) or 0.0)
        return_pct = float(record.get("return_pct", 0.0) or 0.0)
        fees_paid = float(record.get("fee_paid", 0.0) or 0.0)
        current["realized_pnl"] += realized_pnl
        current["return_pct_total"] += return_pct
        current["fees_paid"] += fees_paid
        if realized_pnl > 0:
            current["wins"] += 1
        elif realized_pnl < 0:
            current["losses"] += 1

    rows: List[Dict[str, Any]] = []
    for group_key, values in grouped.items():
        sell_events = int(values["sell_events"])
        realized_pnl = float(values["realized_pnl"])
        return_pct_total = float(values["return_pct_total"])
        rows.append(
            {
                field_name: group_key,
                "sell_events": sell_events,
                "wins": int(values["wins"]),
                "losses": int(values["losses"]),
                "win_rate_pct": round((values["wins"] / sell_events) * 100, 2) if sell_events else 0.0,
                "realized_pnl": round(realized_pnl, 2),
                "avg_realized_pnl": round(realized_pnl / sell_events, 4) if sell_events else 0.0,
                "avg_return_pct": round(return_pct_total / sell_events, 4) if sell_events else 0.0,
                "fees_paid": round(float(values["fees_paid"]), 2),
            }
        )

    return sorted(rows, key=lambda item: (-item["realized_pnl"], item[field_name]))


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    with path.open("w", encoding="utf-8", newline="") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def analyze_trade_quality(trade_rows: List[Dict[str, str]]) -> Dict[str, Any]:
    open_entries_by_pair: Dict[str, Dict[str, Any]] = {}
    analyzed_sell_records: List[Dict[str, Any]] = []

    for row in trade_rows:
        pair = row.get("pair") or "UNKNOWN"
        side = row.get("side") or ""
        signal_reason = row.get("signal_reason") or ""
        strategy = row.get("strategy") or "unknown"
        strategy_debug = _parse_strategy_debug(row.get("strategy_debug", ""))

        if side == "BUY":
            open_entries_by_pair[pair] = {
                "pair": pair,
                "strategy": strategy,
                "entry_timestamp": row.get("timestamp"),
                "entry_signal_reason": signal_reason,
                "entry_rsi": _parse_float(strategy_debug.get("rsi")),
                "entry_volatility": _parse_float(strategy_debug.get("volatility")),
                "entry_distance_to_mid_pct": _parse_float(strategy_debug.get("distance_to_mid_pct")),
                "entry_required_distance_to_mid_pct": _parse_float(
                    strategy_debug.get("required_distance_to_mid_pct")
                ),
                "entry_trend_distance_pct": _parse_float(strategy_debug.get("trend_distance_pct")),
                "entry_edge_surplus_pct": (
                    (_parse_float(strategy_debug.get("distance_to_mid_pct")) or 0.0)
                    - (_parse_float(strategy_debug.get("required_distance_to_mid_pct")) or 0.0)
                ),
                "entry_price": _parse_float(row.get("price")),
            }
            continue

        if side != "SELL":
            continue

        matched_entry = open_entries_by_pair.get(pair, {})
        realized_pnl = _parse_float(row.get("realized_pnl")) or 0.0
        return_pct = _parse_float(row.get("return_pct")) or 0.0
        fee_paid = _parse_float(row.get("fee_paid")) or 0.0

        analyzed_record = {
            "pair": pair,
            "strategy": strategy,
            "exit_timestamp": row.get("timestamp"),
            "exit_reason": signal_reason,
            "realized_pnl": realized_pnl,
            "return_pct": return_pct,
            "fee_paid": fee_paid,
            "entry_timestamp": matched_entry.get("entry_timestamp"),
            "entry_signal_reason": matched_entry.get("entry_signal_reason", "unknown"),
            "entry_rsi_bucket": _bucket_rsi(matched_entry.get("entry_rsi")),
            "entry_volatility_bucket": _bucket_label(
                matched_entry.get("entry_volatility"),
                [0.008, 0.012, 0.016, 0.02],
            ),
            "entry_distance_to_mid_bucket": _bucket_label(
                matched_entry.get("entry_distance_to_mid_pct"),
                [0.004, 0.006, 0.008, 0.012],
            ),
            "entry_edge_surplus_bucket": _bucket_label(
                matched_entry.get("entry_edge_surplus_pct"),
                [0.0, 0.002, 0.004, 0.008],
            ),
            "entry_trend_distance_bucket": _bucket_label(
                matched_entry.get("entry_trend_distance_pct"),
                [0.5, 1.0, 1.5, 2.0],
                precision=2,
            ),
        }
        analyzed_sell_records.append(analyzed_record)

        position_fully_closed = str(row.get("position_fully_closed", "")).lower() == "true"
        if position_fully_closed:
            open_entries_by_pair.pop(pair, None)

    summary = {
        "sell_records_analyzed": len(analyzed_sell_records),
        "open_entries_remaining": len(open_entries_by_pair),
        "totals": {
            "realized_pnl": round(sum(record["realized_pnl"] for record in analyzed_sell_records), 2),
            "fees_paid": round(sum(record["fee_paid"] for record in analyzed_sell_records), 2),
            "wins": sum(1 for record in analyzed_sell_records if record["realized_pnl"] > 0),
            "losses": sum(1 for record in analyzed_sell_records if record["realized_pnl"] < 0),
        },
        "by_pair": _summarize_records(analyzed_sell_records, "pair"),
        "by_exit_reason": _summarize_records(analyzed_sell_records, "exit_reason"),
        "by_entry_signal_reason": _summarize_records(analyzed_sell_records, "entry_signal_reason"),
        "by_entry_rsi_bucket": _summarize_records(analyzed_sell_records, "entry_rsi_bucket"),
        "by_entry_volatility_bucket": _summarize_records(analyzed_sell_records, "entry_volatility_bucket"),
        "by_entry_distance_to_mid_bucket": _summarize_records(
            analyzed_sell_records,
            "entry_distance_to_mid_bucket",
        ),
        "by_entry_edge_surplus_bucket": _summarize_records(
            analyzed_sell_records,
            "entry_edge_surplus_bucket",
        ),
        "by_entry_trend_distance_bucket": _summarize_records(
            analyzed_sell_records,
            "entry_trend_distance_bucket",
        ),
    }
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze backtest trade quality buckets from exported trade CSVs.")
    parser.add_argument(
        "trade_csv",
        nargs="?",
        default="logs/backtest_trades.csv",
        help="Path to a backtest trade CSV export. Defaults to logs/backtest_trades.csv",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for analysis JSON/CSV outputs. Defaults to artifacts/analysis",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    trade_csv_path = Path(args.trade_csv)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = _load_trade_rows(trade_csv_path)
    summary = analyze_trade_quality(rows)

    json_path = output_dir / "trade_quality_summary.json"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    csv_outputs = {
        "trade_quality_by_pair.csv": summary["by_pair"],
        "trade_quality_by_exit_reason.csv": summary["by_exit_reason"],
        "trade_quality_by_entry_signal_reason.csv": summary["by_entry_signal_reason"],
        "trade_quality_by_entry_rsi_bucket.csv": summary["by_entry_rsi_bucket"],
        "trade_quality_by_entry_volatility_bucket.csv": summary["by_entry_volatility_bucket"],
        "trade_quality_by_entry_distance_to_mid_bucket.csv": summary["by_entry_distance_to_mid_bucket"],
        "trade_quality_by_entry_edge_surplus_bucket.csv": summary["by_entry_edge_surplus_bucket"],
        "trade_quality_by_entry_trend_distance_bucket.csv": summary["by_entry_trend_distance_bucket"],
    }
    for filename, table_rows in csv_outputs.items():
        _write_csv(output_dir / filename, table_rows)

    print(
        json.dumps(
            {
                "input_trade_csv": str(trade_csv_path.resolve()),
                "output_dir": str(output_dir.resolve()),
                "sell_records_analyzed": summary["sell_records_analyzed"],
                "totals": summary["totals"],
                "summary_json_path": str(json_path.resolve()),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
