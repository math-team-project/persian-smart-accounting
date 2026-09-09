"""
main.py
----------------
CLI entry point: input file -> extraction -> LLM summary -> formatted output.

Usage:
    python main.py --input report.pdf --output-format docx
    python main.py --input report.doc --output-format pdf --model claude-sonnet-4-6 \
                    --temperature 0.2 --max-tokens 4000
    python main.py --input report.docx --output out/summary.docx --stream

Environment:
    B_AI_API_KEY must be set (or pass --api-key, not recommended for shared machines).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from doc_writer import convert_docx_to_pdf, render_summary_to_docx
from file_ingest import ExtractionError, extract_text
from llm_client import LLMClientError, LLMConfig, build_summary_prompt, call_llm


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize an audit report (PDF/DOC/DOCX) into a plain-language, "
                    "committee-ready document."
    )
    parser.add_argument("--input", "-i", required=True, help="Path to the source PDF/DOC/DOCX file.")
    parser.add_argument(
        "--output", "-o", default=None,
        help="Output file path. If omitted, derived from --input and --output-format."
    )
    parser.add_argument(
        "--output-format", choices=["docx", "pdf"], default="docx",
        help="Final document format (default: docx)."
    )
    parser.add_argument("--organization", default=None, help="Organization name for the cover page.")
    parser.add_argument(
        "--meeting-context", default=None,
        help="Free-text note for the cover page, e.g. 'برای نشست هیات امنا - مهر ۱۴۰۴'."
    )
    parser.add_argument("--language", choices=["fa", "en"], default="fa", help="Summary language (default: fa).")

    # --- LLM tunables -------------------------------------------------- #
    parser.add_argument("--model", default=LLMConfig.model, help="Model name (api.b.ai provider).")
    parser.add_argument("--temperature", type=float, default=LLMConfig.temperature)
    parser.add_argument("--max-tokens", type=int, default=LLMConfig.max_tokens)
    parser.add_argument("--stream", action="store_true", help="Use streaming API responses.")
    parser.add_argument("--api-key", default=None, help="Override B_AI_API_KEY env var.")

    return parser


def run(args: argparse.Namespace) -> Path:
    input_path = Path(args.input)

    print(f"[1/4] در حال استخراج متن از: {input_path.name}")
    try:
        extracted = extract_text(input_path)
    except (FileNotFoundError, ExtractionError) as e:
        print(f"خطا در استخراج متن: {e}", file=sys.stderr)
        raise

    for w in extracted.warnings:
        print(f"  هشدار: {w}")

    prompt_text = extracted.as_prompt_text(max_chars=100_000)
    print(f"  {len(prompt_text)} کاراکتر متن استخراج شد.")

    print("[2/4] در حال ساخت prompt و فراخوانی مدل...")
    prompt = build_summary_prompt(
        prompt_text, language=args.language, organization_name=args.organization
    )
    config = LLMConfig(
        model=args.model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        stream=args.stream,
        api_key=args.api_key,
    )
    try:
        summary = call_llm(prompt, config)
    except LLMClientError as e:
        print(f"خطا در تماس با LLM: {e}", file=sys.stderr)
        raise

    print("[3/4] در حال ساخت سند خروجی...")
    if args.output:
        output_path = Path(args.output)
    else:
        suffix = ".docx" if args.output_format == "docx" else ".pdf"
        output_path = input_path.with_name(input_path.stem + "_summary").with_suffix(suffix)

    docx_target = output_path if output_path.suffix.lower() == ".docx" else output_path.with_suffix(".docx")
    docx_target.parent.mkdir(parents=True, exist_ok=True)

    render_summary_to_docx(
        summary,
        docx_target,
        organization=args.organization,
        meeting_context=args.meeting_context,
        source_filename=input_path.name,
    )

    final_path = docx_target
    if args.output_format == "pdf":
        print("[4/4] در حال تبدیل به PDF...")
        final_path = convert_docx_to_pdf(docx_target, output_dir=output_path.parent)
        if output_path.suffix.lower() == ".pdf" and final_path != output_path:
            final_path = final_path.rename(output_path)
    else:
        print("[4/4] فرمت خروجی DOCX است؛ نیازی به تبدیل نیست.")

    print(f"\nگزارش نهایی آماده شد: {final_path}")
    return final_path


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        run(args)
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
