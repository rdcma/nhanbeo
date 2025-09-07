import argparse
import json

from product_qa.pipeline import product_qa_pipeline


def main():
    parser = argparse.ArgumentParser(description="Demo Product QA Pipeline")
    parser.add_argument(
        "--csv",
        default="Current_product_names__with_clean_name_.csv",
        help="Path to CSV file",
    )
    parser.add_argument(
        "--api_url",
        help="If provided, load products from this API instead of CSV",
    )
    parser.add_argument(
        "--embed",
        action="store_true",
        help="Build embedding index (slow on first run)",
    )
    parser.add_argument("--q", required=False, help="User query in Vietnamese")
    parser.add_argument(
        "--priority_json",
        help="Path to a JSON file containing preferred display_ids (see API example)",
    )
    args = parser.parse_args()

    preferred_ids = None
    if args.priority_json:
        try:
            with open(args.priority_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Kỳ vọng cấu trúc như ví dụ API: orders[*].items[*].display_id
            s = set()
            for od in data.get("orders", []):
                for it in od.get("items", []):
                    did = it.get("display_id") or it.get("product_display_id")
                    if did:
                        s.add(str(did))
            preferred_ids = s if s else None
        except Exception as e:
            print(f"Không đọc được priority_json: {e}")

    pipeline = product_qa_pipeline(
        csv_path=None if args.api_url else args.csv,
        build_embedding=args.embed,
        preferred_ids=preferred_ids,
        api_url=args.api_url,
    )
    query = args.q
    if not query:
        try:
            query = input("Nhập câu hỏi (vi-VN), ví dụ: 'cho tôi nồi ủ, nồi cơm điện': ").strip()
        except KeyboardInterrupt:
            print("\nHủy.")
            return
    if not query:
        print("Không có câu hỏi. Ví dụ chạy: python run_demo.py --q 'cho tôi nồi ủ'\n")
        return

    result = pipeline(query)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


