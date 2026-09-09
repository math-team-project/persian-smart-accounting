# خلاصه‌ساز گزارش حسابرسی برای جلسات کمیته (Audit Report Summarizer)

سرویسی که یک گزارش حسابرسی (PDF یا Word) را می‌گیرد، با کمک یک مدل زبانی
(از طریق endpoint سازگار با OpenAI در `api.b.ai`) خلاصه‌ای رسمی و قابل‌فهم برای
غیرحسابداران تولید می‌کند، و آن را در قالب یک سند Word یا PDF آماده جلسه
ذخیره می‌کند.

خروجی به‌طور خاص شامل بخش **«موارد نیازمند بررسی و تصمیم‌گیری در جلسه»**
است تا اعضای کمیته دقیقاً بدانند باید روی چه چیزهایی تمرکز کنند.

---

## ساختار پروژه

```
audit_summarizer/
├── file_ingest.py      # استخراج متن از PDF/DOC/DOCX
├── llm_client.py       # فراخوانی Anthropic API + ساخت prompt
├── doc_writer.py        # ساخت سند Word (RTL/فارسی) + تبدیل به PDF
├── main.py               # CLI که همه را به هم وصل می‌کند
├── requirements.txt
├── tests/
│   └── test_pipeline.py # تست‌های واحد (بدون نیاز به API واقعی)
└── samples/
    ├── run_demo.py       # اجرای کامل pipeline روی یک نمونه واقعی
    └── report.doc        # نمونه گزارش حسابرسی که ارسال شد
```

## نصب

```bash
python -m venv .venv
source .venv/bin/activate          # ویندوز: .venv\Scripts\activate
pip install -r requirements.txt
```

برای فایل‌های `.doc` قدیمی (نه `.docx`)، **LibreOffice** باید روی سیستم نصب باشد
(برای تبدیل داخلی به `.docx` قبل از استخراج متن؛ همان‌طور که برای تبدیل خروجی
نهایی به PDF هم استفاده می‌شود):

```bash
# Ubuntu/Debian
sudo apt-get install libreoffice
# macOS
brew install --cask libreoffice
```

## تنظیم کلید API

کلید را همیشه از طریق متغیر محیطی تنظیم کنید، نه داخل کد (کلیدهای هاردکد شده در
فایل‌های `.py` به‌راحتی در گیت، لاگ‌ها یا چت‌ها لو می‌روند):

```bash
export B_AI_API_KEY="sk-..."
```

## اجرا (CLI)

```bash
python main.py --input report.pdf --output-format docx
```

خروجی‌های قابل‌تنظیم:

| گزینه | توضیح | پیش‌فرض |
|---|---|---|
| `--input / -i` | مسیر فایل ورودی (`.pdf`, `.doc`, `.docx`, `.rtf`, `.odt`) | (الزامی) |
| `--output / -o` | مسیر فایل خروجی | برگرفته از نام فایل ورودی |
| `--output-format` | `docx` یا `pdf` | `docx` |
| `--organization` | نام سازمان برای جلد گزارش | - |
| `--meeting-context` | یک خط توضیح روی جلد (مثلاً «برای نشست هیأت امنا») | - |
| `--language` | `fa` یا `en` | `fa` |
| `--model` | نام مدل (پروایدر `api.b.ai`) | `deepseek-v4-flash` |
| `--temperature` | خلاقیت مدل (برای محتوای حسابرسی، پایین توصیه می‌شود) | `0.3` |
| `--max-tokens` | حداکثر طول پاسخ | `1000` (برای گزارش‌های بلندتر افزایش دهید) |
| `--stream` | استفاده از پاسخ استریم‌شده (پیش‌فرض همین پروایدر روشن است) | روشن |

مثال کامل:

```bash
python main.py \
  --input "گزارش_حسابرسی_1398.doc" \
  --output-format pdf \
  --organization "پارک علم و فناوری خراسان" \
  --meeting-context "برای نشست هیأت امنا - سال مالی ۱۳۹۸" \
  --temperature 0.2 \
  --max-tokens 4000
```

> توجه: پیش‌فرض `max-tokens` روی این پروایدر ۱۰۰۰ است تا با نمونه شما مطابق باشد،
> اما برای خلاصه‌سازی یک گزارش چندصفحه‌ای معمولاً باید آن را به ۳۰۰۰ تا ۴۰۰۰ افزایش
> دهید، وگرنه پاسخ ممکن است ناقص/بریده برگردد.

## استفاده به‌صورت کتابخانه (بدون CLI)

```python
from file_ingest import extract_text
from llm_client import LLMConfig, build_summary_prompt, call_llm
from doc_writer import render_summary_to_docx, convert_docx_to_pdf

extracted = extract_text("report.pdf")
prompt = build_summary_prompt(extracted.as_prompt_text(), organization_name="نام سازمان")
summary = call_llm(prompt, LLMConfig(temperature=0.2, max_tokens=4000))

docx_path = render_summary_to_docx(summary, "summary.docx", organization="نام سازمان")
pdf_path = convert_docx_to_pdf(docx_path)   # اختیاری
```

## تست‌ها

```bash
pytest -q
```

تست‌ها فراخوانی واقعی به API انجام نمی‌دهند (`requests.post` mock می‌شود)، پس بدون
هزینه و بدون نیاز به اتصال اینترنت اجرا می‌شوند.

## دمو با نمونه واقعی

`samples/run_demo.py` کل pipeline را روی نمونه گزارش حسابرسی واقعی (`samples/report.doc`،
همان فایلی که برای شما ارسال شد) اجرا می‌کند. چون این محیط توسعه فعلاً `B_AI_API_KEY`
ندارد، `call_llm` در این اسکریپت با یک پاسخ نمونه (که دقیقاً از ساختار خواسته‌شده در
prompt پیروی می‌کند) جایگزین شده - اما استخراج متن، ساخت prompt، رندر Word با راست‌چین
فارسی، و تبدیل به PDF همگی واقعی هستند:

```bash
python samples/run_demo.py
```

خروجی در `samples/demo_output/` ساخته می‌شود. برای اجرای واقعی با API زنده، به‌جای این
اسکریپت مستقیماً `main.py` را با `B_AI_API_KEY` تنظیم‌شده اجرا کنید.

## نکات مهم درباره کیفیت خروجی

- **فونت فارسی:** سند خروجی با فونت «B Nazanin» تنظیم شده. اگر روی سیستمی که Word
  را باز می‌کند این فونت نصب نباشد، Word به‌طور خودکار جایگزین می‌کند؛ اگر می‌خواهید
  فونت دیگری (مثلاً «IRANSans» یا «Vazirmatn») استفاده شود، مقدار `RTL_FONT` در
  `doc_writer.py` را تغییر دهید.
- **اسکن‌های تصویری:** اگر PDF ورودی یک اسکن بدون لایه متنی باشد، `file_ingest.py`
  خطای واضح می‌دهد (این نسخه هنوز OCR انجام نمی‌دهد - به بخش «توسعه‌های بعدی» مراجعه کنید).
- **صحت اعداد:** طبق دستور صریح در prompt، مدل باید مبالغ و تاریخ‌ها را دقیقاً از متن
  اصلی منتقل کند نه حدس بزند؛ همچنان توصیه می‌شود قبل از جلسه، اعداد کلیدی خلاصه با
  سند اصلی مطابقت داده شود.

## توسعه‌های بعدی (اختیاری، خارج از MVP)

- **OCR** برای PDFهای اسکن‌شده (مثلاً با `pytesseract` یا Textract).
- **پردازش دسته‌ای (batch):** حلقه‌زدن روی یک پوشه از گزارش‌ها.
- **احراز هویت / API سرویس‌دهنده:** بسته‌بندی `main.py` به‌صورت یک FastAPI endpoint
  با آپلود فایل (`UploadFile`) و کلید API در header، به‌جای اجرای صرفاً CLI.
- **استقرار (Deployment):** Docker image شامل Python + LibreOffice headless
  (برای پشتیبانی `.doc`/PDF→conversion)، پشت یک صف کار (queue) برای فایل‌های بزرگ،
  و ذخیره خروجی در object storage (S3/MinIO) به‌جای دیسک محلی.
- **کش کردن پاسخ مدل** بر اساس هش محتوای ورودی، برای جلوگیری از فراخوانی تکراری API
  روی همان فایل.
