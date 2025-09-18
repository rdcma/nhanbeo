import argparse
import json
from typing import Any, List
from product_qa.address_normalizer import (
    process_file,
    summarize_results,
    build_admin_client_from_env,
    normalize_record,
    _is_progress_enabled,
    _truncate_raw,
)


def main():
    parser = argparse.ArgumentParser(description="Normalize VN addresses from JSON file")
    parser.add_argument("--input", required=True, help="Path to input JSON list with 'raw' fields")
    parser.add_argument("--output", required=False, help="Path to output JSON")
    args = parser.parse_args()

    # Load input; support both list of strings and list of objects with 'raw'
    results: List[dict]
    try:
        with open(args.input, "r", encoding="utf-8") as f:
            data: Any = json.load(f)
    except Exception:
        data = None

    if isinstance(data, list) and (len(data) == 0 or isinstance(data[0], str)):
        # Directly process list of raw strings
        api_client = build_admin_client_from_env()
        results = []
        total = len(data)
        correct_so_far = 0
        for idx, s in enumerate(data, start=1):
            if not isinstance(s, str):
                continue
            item = normalize_record(s, api_client)
            results.append(item)
            # live progress
            if _is_progress_enabled():
                from product_qa.address_normalizer import evaluate_result_item
                try:
                    if evaluate_result_item(item):
                        correct_so_far += 1
                except Exception:
                    pass
                ratio = (correct_so_far / idx * 100.0) if idx else 0.0
                print(f"[{idx}/{total}] {round(ratio,2)}% ok | raw: {_truncate_raw(s)}", flush=True)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
    else:
        results = process_file(args.input, args.output)
    print(f"Processed {len(results)} items")
    summary = summarize_results(results)
    print(f"Summary: {summary['correct']}/{summary['total']} = {summary['ratio_percent']}% correct")


if __name__ == "__main__":
    main()


