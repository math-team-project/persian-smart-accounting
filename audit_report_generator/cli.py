"""رابط خط فرمان (CLI) پکیج.

نمونه استفاده:
    python -m audit_report_generator.cli \\
        --doc-text-file sample_data/audit_report.txt \\
        --checklist-json-file sample_data/checklist_false_items.json \\
        --output output/گزارش_کمیسیون.docx \\
        --model gpt-4o-mini \\
        --base-url https://api.openai.com/v1

کلید API را می‌توانید با --api-key بدهید یا در متغیر محیطی AUDIT_LLM_API_KEY
(یا OPENAI_API_KEY) قرار دهید.
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import LLMConfig
from .exceptions import AuditReportError
from .report_generator import generate_committee_report


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="تولید گزارش موارد عدم تطابق چک‌لیست حسابرسی جهت جلسه کمیسیون (docx)."
    )
    parser.add_argument(
        "--doc-text-file",
        default=None,
        help="مسیر فایل متنی گزارش حسابرسی (txt) — اختیاری؛ اگر داده نشود، گزارش فقط بر اساس چک‌لیست ساخته می‌شود",
    )
    parser.add_argument("--checklist-json-file", required=True, help="مسیر فایل JSON چک‌لیست")
    parser.add_argument("--output", default="output/گزارش_کمیسیون.docx", help="مسیر فایل خروجی docx")
    parser.add_argument("--entity-name", default=None, help="نام سازمان/شرکت برای درج در سربرگ")
    parser.add_argument(
        "--title",
        default="گزارش موارد عدم تطابق چک‌لیست حسابرسی جهت جلسه کمیسیون",
        help="عنوان گزارش",
    )
    parser.add_argument("--api-key", default=None, help="کلید API (در صورت نبود، از متغیر محیطی خوانده می‌شود)")
    parser.add_argument("--base-url", default=None, help="آدرس پایه provider (اختیاری)")
    parser.add_argument("--model", default="gpt-4o-mini", help="نام مدل")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("-v", "--verbose", action="store_true", help="نمایش لاگ‌های جزئی")
    return parser


def main(argv=None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    doc_text = None
    if args.doc_text_file:
        try:
            with open(args.doc_text_file, "r", encoding="utf-8") as f:
                doc_text = f.read()
        except OSError as exc:
            print(f"خطا در خواندن فایل متن گزارش حسابرسی: {exc}", file=sys.stderr)
            return 1

    try:
        with open(args.checklist_json_file, "r", encoding="utf-8") as f:
            checklist_json = f.read()
    except OSError as exc:
        print(f"خطا در خواندن فایل JSON چک‌لیست: {exc}", file=sys.stderr)
        return 1

    config = LLMConfig(
        api_key=args.api_key,
        base_url=args.base_url,
        model=args.model,
        temperature=args.temperature,
        max_retries=args.max_retries,
    )

    try:
        output_path = generate_committee_report(
            doc_text=doc_text,
            checklist_json=checklist_json,
            config=config,
            output_path=args.output,
            report_title=args.title,
            entity_name=args.entity_name,
        )
    except AuditReportError as exc:
        print(f"خطا: {exc}", file=sys.stderr)
        return 1

    print(f"گزارش با موفقیت ساخته شد: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
