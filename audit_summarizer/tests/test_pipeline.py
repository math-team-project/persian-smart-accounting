"""
Simple unit tests. Run with:  pytest -q
These do NOT call the real Anthropic API (call_llm is monkeypatched / mocked),
so they run offline and free of charge.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import docx  # python-docx, used to fabricate a test .docx

from doc_writer import render_summary_to_docx
from file_ingest import ExtractedDocument, ExtractionError, extract_text
from llm_client import LLMConfig, build_summary_prompt, call_llm


# --------------------------------------------------------------------------- #
# file_ingest
# --------------------------------------------------------------------------- #

def test_extract_missing_file_raises(tmp_path):
    missing = tmp_path / "nope.pdf"
    try:
        extract_text(missing)
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_extract_unsupported_format_raises(tmp_path):
    bogus = tmp_path / "report.txt"
    bogus.write_text("hello")
    try:
        extract_text(bogus)
        assert False, "expected ExtractionError"
    except ExtractionError:
        pass


def test_extract_docx_reads_paragraphs_and_tables(tmp_path):
    path = tmp_path / "sample.docx"
    d = docx.Document()
    d.add_paragraph("این یک گزارش آزمایشی است.")
    table = d.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "مبلغ"
    table.cell(0, 1).text = "1,000,000 ریال"
    d.save(str(path))

    extracted = extract_text(path)
    assert "گزارش آزمایشی" in extracted.text
    assert extracted.tables and extracted.tables[0][0][0] == "مبلغ"
    assert not extracted.is_empty


def test_extracted_document_as_prompt_text_truncates():
    doc = ExtractedDocument(source_path=Path("x.pdf"), text="ا" * 500)
    prompt_text = doc.as_prompt_text(max_chars=50)
    assert len(prompt_text) <= 50 + len("\n\n[...متن به دلیل محدودیت طول کوتاه شد / truncated...]")
    assert "truncated" in prompt_text


# --------------------------------------------------------------------------- #
# llm_client
# --------------------------------------------------------------------------- #

def test_build_summary_prompt_includes_report_text_and_sections():
    prompt = build_summary_prompt("متن نمونه گزارش حسابرسی", organization_name="پارک فناوری نمونه")
    assert "متن نمونه گزارش حسابرسی" in prompt
    assert "موارد نیازمند بررسی و تصمیم‌گیری در جلسه" in prompt
    assert "پارک فناوری نمونه" in prompt


@patch("llm_client.requests.post")
def test_call_llm_non_streaming_parses_response(mock_post):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "## خلاصه مدیریتی\nمتن تست"}}]
    }
    mock_post.return_value = mock_response

    config = LLMConfig(api_key="test-key", stream=False)
    result = call_llm("prompt text", config)
    assert "خلاصه مدیریتی" in result

    # Confirm request skeleton shape
    _, kwargs = mock_post.call_args
    payload = kwargs["json"]
    assert payload["model"] == config.model
    assert payload["max_tokens"] == config.max_tokens
    assert payload["messages"][0]["role"] == "user"
    assert kwargs["headers"]["Authorization"] == "test-key"


@patch("llm_client.requests.post")
def test_call_llm_streaming_reassembles_deltas(mock_post):
    sse_lines = [
        'data: {"choices":[{"delta":{"content":"## خلاصه"}}]}',
        'data: {"choices":[{"delta":{"content":" مدیریتی"}}]}',
        "data: [DONE]",
    ]
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_lines.return_value = sse_lines
    mock_post.return_value = mock_response

    config = LLMConfig(api_key="test-key", stream=True)
    result = call_llm("prompt text", config)
    assert result == "## خلاصه مدیریتی"

    _, kwargs = mock_post.call_args
    assert kwargs["json"]["stream"] is True
    assert kwargs["stream"] is True


@patch("llm_client.requests.post")
def test_call_llm_raises_on_http_error(mock_post):
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "unauthorized"
    mock_post.return_value = mock_response

    config = LLMConfig(api_key="bad-key")
    try:
        call_llm("prompt", config)
        assert False, "expected LLMClientError"
    except Exception as e:
        assert "401" in str(e)


def test_call_llm_without_api_key_raises(monkeypatch):
    monkeypatch.delenv("B_AI_API_KEY", raising=False)
    config = LLMConfig(api_key=None)
    try:
        call_llm("prompt", config)
        assert False, "expected LLMClientError about missing key"
    except Exception as e:
        assert "API" in str(e)


# --------------------------------------------------------------------------- #
# doc_writer
# --------------------------------------------------------------------------- #

def test_render_summary_to_docx_creates_file(tmp_path):
    summary_md = (
        "## خلاصه مدیریتی\n"
        "این یک خلاصه آزمایشی است.\n\n"
        "## یافته‌های کلیدی\n"
        "- یافته اول\n"
        "- یافته دوم\n\n"
        "## موارد نیازمند بررسی و تصمیم‌گیری در جلسه\n"
        "1. آیا مصوبه X باید تایید شود؟\n"
    )
    out_path = tmp_path / "summary.docx"
    result_path = render_summary_to_docx(
        summary_md, out_path, organization="سازمان نمونه", source_filename="input.pdf"
    )
    assert result_path.exists()

    # Round-trip read to sanity check content landed in the document.
    doc = docx.Document(str(result_path))
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "خلاصه مدیریتی" in full_text
    assert "یافته اول" in full_text
