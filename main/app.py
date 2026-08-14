"""
داشبورد هوشمند حسابرسی و حسابداری (Persian Smart Accounting)
================================================================
اپلیکیشن اصلی داشبورد که با Streamlit ساخته شده و گردش‌کاری تعریف‌شده در
main/main.ipynb را روی فایل‌های اکسل آپلودشده توسط کاربر اجرا می‌کند:

    ۱) استخراج فرم‌های بودجه اصلاحیه / ابلاغ / تاییدیه
    ۲) استخراج صورت‌های مالی و ترازنامه
    ۳) اجرای کامل چک‌لیست حسابرسی (طبق extraction_script/scripts/checklist)
    ۴) نمایش نتایج در قالب داشبورد فارسی و راست‌به‌چپ (RTL) با تم تیره

اجرا:
    streamlit run main/app.py
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from icons import icon  # noqa: E402
from pipeline import (  # noqa: E402
    FILE_SLOTS,
    STATUS_META,
    PipelineError,
    cleanup_workdir,
    flatten_sheets_for_preview,
    make_temp_workdir,
    run_full_pipeline,
    save_uploaded_file,
)
from styles import (  # noqa: E402
    ACCENT,
    ACCENT_SKY,
    BG_ELEVATED,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    inject_custom_css,
)

st.set_page_config(
    page_title="داشبورد هوشمند حسابرسی و حسابداری",
    page_icon=":material/fact_check:",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_custom_css(st)

if "result" not in st.session_state:
    st.session_state["result"] = None

# نگاشت وضعیت سوالات چک‌لیست به آیکون Material برای عنوان expander (که HTML خام نمی‌پذیرد)
EXPANDER_ICON = {
    "TRUE": "check_circle",
    "FALSE": "cancel",
    "ERROR": "warning",
    "MANUAL": "visibility",
}
BREAKDOWN_LABEL = {True: "درست", False: "نادرست"}


def render_alert(icon_name: str, kind: str, message: str) -> None:
    """جعبه‌ی اعلان سفارشی با آیکون SVG به‌جای ایموجی/پونز."""
    st.markdown(
        f"""
        <div class="psa-alert {kind}">
            {icon(icon_name, 22)}
            <div class="psa-alert-text">{message}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def style_plotly(fig: go.Figure, height: int | None = None) -> go.Figure:
    """اعمال تم تیره‌ی یکنواخت روی نمودارهای Plotly."""
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=TEXT_PRIMARY, family="Vazirmatn, Tahoma, sans-serif"),
        legend=dict(font=dict(color=TEXT_SECONDARY)),
    )
    if height:
        fig.update_layout(height=height)
    return fig


# ---------------------------------------------------------------------------
# نوار کناری
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(
        f"""
        <div class="psa-sidebar-brand">
            <div class="psa-icon-badge">{icon('shield-check', 20)}</div>
            <div>
                <div class="psa-sidebar-title">داشبورد هوشمند حسابرسی</div>
                <div class="psa-sidebar-subtitle">تحلیل بودجه، صورت‌های مالی و چک‌لیست حسابرسی</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.divider()
    st.markdown('<div class="psa-section-title" style="font-size:.92rem;">راهنمای سریع</div>', unsafe_allow_html=True)

    steps = [
        "فایل‌های الزامی (بودجه اصلاحیه، صورت‌های مالی و ترازنامه) را بارگذاری کنید.",
        "در صورت وجود، فایل‌های تاییدیه اعتبارات و قانون بودجه را نیز اضافه کنید.",
        "روی دکمه «پردازش و تحلیل داده‌ها» بزنید.",
        "نتایج را در تب‌های نمای کلی، چک‌لیست، داده‌ها و خروجی گزارش ببینید.",
    ]
    steps_html = "".join(
        f"""<li class="psa-step">
                <div class="psa-step-num">{i}</div>
                <div class="psa-step-text">{text}</div>
            </li>"""
        for i, text in enumerate(steps, start=1)
    )
    st.markdown(f"<ul>{steps_html}</ul>", unsafe_allow_html=True)

    st.divider()
    if st.session_state["result"] is not None:
        if st.button(
            "پاک‌کردن نتایج و شروع مجدد",
            icon=":material/restart_alt:",
            width="stretch",
        ):
            st.session_state["result"] = None
            st.rerun()
        st.divider()
    st.caption("منطق پردازش برگرفته از main/main.ipynb و ساختار خروجی چک‌لیست مطابق checklist_process.md است.")


# ---------------------------------------------------------------------------
# هدر
# ---------------------------------------------------------------------------
st.markdown(
    f"""
    <div class="psa-hero">
        <div class="psa-hero-top">
            <div class="psa-hero-badge">{icon('shield-check', 26)}</div>
            <h1>داشبورد هوشمند حسابرسی و حسابداری</h1>
        </div>
        <p class="psa-hero-desc">
            فایل‌های بودجه، صورت‌های مالی و ترازنامه را بارگذاری کنید تا چک‌لیست حسابرسی به‌صورت
            خودکار اجرا شود؛ مغایرت‌ها کشف، تطابق ارقام پیگیری و گزارش نهایی تولید می‌شود — همه در یک نگاه.
        </p>
        <div class="psa-badges">
            <span class="psa-chip">{icon('sparkles', 15)} تحلیل خودکار</span>
            <span class="psa-chip">{icon('list-checks', 15)} چک‌لیست هوشمند حسابرسی</span>
            <span class="psa-chip">{icon('file-down', 15)} خروجی اکسل / JSON</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# بخش بارگذاری فایل‌ها
# ---------------------------------------------------------------------------
st.markdown(
    f'<div class="psa-section-title">{icon("upload-cloud", 20)} بارگذاری فایل‌های ورودی</div>',
    unsafe_allow_html=True,
)

required_keys = [k for k, v in FILE_SLOTS.items() if v["required"]]
optional_keys = [k for k, v in FILE_SLOTS.items() if not v["required"]]

uploaded_files: dict[str, object] = {}


def _render_upload_card(col, key: str) -> None:
    slot = FILE_SLOTS[key]
    is_required = slot["required"]
    badge_class = "required" if is_required else "optional"
    badge_text = "الزامی" if is_required else "اختیاری"
    badge_icon = "asterisk" if is_required else "circle"

    current_value = st.session_state.get(f"upload_{key}")
    status_class = "uploaded" if current_value is not None else "empty"
    status_text = "بارگذاری شد" if current_value is not None else "خالی"
    status_icon = "check-circle" if current_value is not None else "circle"

    with col:
        with st.container(border=True):
            st.markdown(
                f"""
                <div class="psa-upload-marker"></div>
                <div class="psa-upload-head">
                    <div class="psa-upload-title">{icon(slot['icon'], 18)}<span>{slot['label']}</span></div>
                    <span class="psa-badge {badge_class}">{icon(badge_icon, 11)}{badge_text}</span>
                </div>
                <p class="psa-upload-help">{slot['help']}</p>
                <span class="psa-badge {status_class}" style="margin-bottom:.5rem;">
                    {icon(status_icon, 11)}{status_text}
                </span>
                """,
                unsafe_allow_html=True,
            )
            uploaded_files[key] = st.file_uploader(
                slot["label"],
                type=["xlsx", "xls"],
                key=f"upload_{key}",
                label_visibility="collapsed",
            )


req_cols = st.columns(len(required_keys))
for col, key in zip(req_cols, required_keys):
    _render_upload_card(col, key)

opt_cols = st.columns(len(optional_keys))
for col, key in zip(opt_cols, optional_keys):
    _render_upload_card(col, key)

st.write("")
process_clicked = st.button(
    "پردازش و تحلیل داده‌ها",
    icon=":material/bolt:",
    type="primary",
    width="stretch",
)

if process_clicked:
    missing_labels = [FILE_SLOTS[k]["label"] for k in required_keys if uploaded_files.get(k) is None]
    if missing_labels:
        st.error("لطفاً فایل‌های الزامی زیر را بارگذاری کنید: " + "، ".join(missing_labels), icon=":material/error:")
    else:
        workdir = make_temp_workdir()
        try:
            with st.spinner("در حال استخراج داده‌ها و اجرای چک‌لیست حسابرسی... این عملیات ممکن است چند ثانیه طول بکشد."):
                file_paths = {}
                for key, uploaded in uploaded_files.items():
                    file_paths[key] = save_uploaded_file(uploaded, workdir) if uploaded is not None else None
                result = run_full_pipeline(file_paths)
            st.session_state["result"] = result
            st.success(
                f"پردازش با موفقیت انجام شد ({result['elapsed_seconds']:.1f} ثانیه).",
                icon=":material/task_alt:",
            )
        except PipelineError as exc:
            st.error(str(exc), icon=":material/error:")
        except Exception as exc:  # noqa: BLE001
            st.error("خطای غیرمنتظره‌ای هنگام پردازش رخ داد.", icon=":material/error:")
            with st.expander("جزئیات فنی خطا"):
                st.exception(exc)
        finally:
            cleanup_workdir(workdir)


# ---------------------------------------------------------------------------
# داشبورد نتایج
# ---------------------------------------------------------------------------
result = st.session_state["result"]

if result is None:
    render_alert(
        "info",
        "info",
        "برای مشاهده داشبورد، ابتدا فایل‌های الزامی را بارگذاری و دکمه «پردازش و تحلیل داده‌ها» را بزنید.",
    )
    st.stop()

for warning in result.get("warnings", []):
    render_alert("alert-triangle", "warning", warning)

summary = result["summary"]
checklist_results = result["checklist_results"]

tab_overview, tab_checklist, tab_data, tab_export = st.tabs(
    [
        ":material/insights: نمای کلی",
        ":material/fact_check: چک‌لیست حسابرسی",
        ":material/database: داده‌های استخراج‌شده",
        ":material/file_download: خروجی گزارش",
    ]
)

# --- تب نمای کلی -----------------------------------------------------------
with tab_overview:
    st.markdown(
        f'<div class="psa-section-title">{icon("layout-dashboard", 20)} شاخص‌های کلیدی عملکرد</div>',
        unsafe_allow_html=True,
    )
    kpi_cols = st.columns(6)
    kpi_data = [
        ("total", "list-checks", "کل سوالات چک‌لیست", summary["total"]),
        ("pass", "check-circle", "تطابق دارد", summary["true_count"]),
        ("fail", "x-circle", "عدم تطابق", summary["false_count"]),
        ("error", "alert-triangle", "خطای پردازش", summary["error_count"]),
        ("manual", "eye", "نیازمند بررسی دستی", summary["manual_count"]),
        ("rate", "gauge", "درصد تطابق", f"{summary['compliance_rate']:.1f}٪"),
    ]
    for col, (variant, icon_name, label, value) in zip(kpi_cols, kpi_data):
        with col:
            st.markdown(
                f"""
                <div class="psa-kpi {variant}">
                    <div class="psa-kpi-top">{icon(icon_name, 20)}</div>
                    <div class="psa-kpi-value">{value}</div>
                    <div class="psa-kpi-label">{label}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    st.write("")
    chart_col1, chart_col2 = st.columns([1.1, 1])

    with chart_col1:
        st.markdown(
            f'<div class="psa-section-title">{icon("pie-chart", 18)} توزیع وضعیت سوالات چک‌لیست</div>',
            unsafe_allow_html=True,
        )
        pie_labels = [STATUS_META[s]["label"] for s in ["TRUE", "FALSE", "ERROR", "MANUAL"]]
        pie_values = [summary["true_count"], summary["false_count"], summary["error_count"], summary["manual_count"]]
        pie_colors = [STATUS_META[s]["color"] for s in ["TRUE", "FALSE", "ERROR", "MANUAL"]]
        fig_pie = go.Figure(
            data=[
                go.Pie(
                    labels=pie_labels,
                    values=pie_values,
                    hole=0.58,
                    marker=dict(colors=pie_colors, line=dict(color=BG_ELEVATED, width=2)),
                    textinfo="label+value",
                    textfont=dict(color=TEXT_PRIMARY),
                    sort=False,
                )
            ]
        )
        fig_pie.update_layout(
            margin=dict(t=10, b=10, l=10, r=10),
            showlegend=True,
            legend=dict(orientation="h", y=-0.15),
            annotations=[
                dict(
                    text=f"{summary['total']}<br>سوال",
                    x=0.5,
                    y=0.5,
                    font_size=16,
                    font_color=TEXT_PRIMARY,
                    showarrow=False,
                )
            ],
        )
        st.plotly_chart(style_plotly(fig_pie), width="stretch")

    with chart_col2:
        st.markdown(
            f'<div class="psa-section-title">{icon("gauge", 18)} نرخ تطابق کلی</div>',
            unsafe_allow_html=True,
        )
        fig_gauge = go.Figure(
            go.Indicator(
                mode="gauge+number",
                value=summary["compliance_rate"],
                number={"suffix": "٪", "font": {"color": TEXT_PRIMARY}},
                gauge={
                    "axis": {"range": [0, 100], "tickcolor": TEXT_SECONDARY},
                    "bar": {"color": ACCENT_SKY},
                    "bgcolor": BG_ELEVATED,
                    "bordercolor": BORDER,
                    "steps": [
                        {"range": [0, 50], "color": "rgba(239, 68, 68, 0.25)"},
                        {"range": [50, 80], "color": "rgba(245, 158, 11, 0.25)"},
                        {"range": [80, 100], "color": "rgba(16, 185, 129, 0.25)"},
                    ],
                },
            )
        )
        fig_gauge.update_layout(margin=dict(t=25, b=10, l=25, r=25), height=280)
        st.plotly_chart(style_plotly(fig_gauge), width="stretch")

    st.markdown(
        f'<div class="psa-section-title">{icon("bar-chart", 18)} شیت‌های استخراج‌شده به تفکیک فایل ورودی</div>',
        unsafe_allow_html=True,
    )
    source_counts = result.get("sheet_source_counts", {})
    if source_counts:
        palette = [ACCENT, ACCENT_SKY, "#8B5CF6", "#F59E0B", "#EF4444"]
        fig_bar = go.Figure(
            go.Bar(
                x=list(source_counts.values()),
                y=list(source_counts.keys()),
                orientation="h",
                text=list(source_counts.values()),
                textposition="outside",
                marker=dict(color=(palette * 3)[: len(source_counts)]),
            )
        )
        fig_bar.update_layout(
            showlegend=False,
            xaxis_title="تعداد شیت استخراج‌شده",
            yaxis_title="",
            margin=dict(t=10, b=10, l=10, r=10),
            xaxis=dict(gridcolor=BORDER, color=TEXT_SECONDARY),
            yaxis=dict(color=TEXT_PRIMARY),
        )
        st.plotly_chart(style_plotly(fig_bar), width="stretch")
    st.caption(f"مجموع شیت‌های بارگذاری‌شده در حافظه: {len(result['imported_sheets'])}")


# --- تب چک‌لیست حسابرسی -----------------------------------------------------
with tab_checklist:
    st.markdown(
        f'<div class="psa-section-title">{icon("list-checks", 20)} فهرست کامل سوالات چک‌لیست حسابرسی</div>',
        unsafe_allow_html=True,
    )

    filter_col1, filter_col2 = st.columns([2, 1])
    with filter_col1:
        search_term = st.text_input(":material/search: جستجو در متن سوال / هدف بررسی", "")
    with filter_col2:
        status_options = ["همه"] + [STATUS_META[s]["label"] for s in ["TRUE", "FALSE", "ERROR", "MANUAL"]]
        status_filter = st.selectbox(":material/filter_list: وضعیت", status_options)

    label_to_status = {v["label"]: k for k, v in STATUS_META.items()}

    filtered_results = []
    for record in checklist_results:
        if status_filter != "همه" and record["status"] != label_to_status[status_filter]:
            continue
        if search_term:
            haystack = " ".join(
                [record["question_text"], record["question_purpose"], record["general_description"]]
            ).lower()
            if search_term.lower() not in haystack:
                continue
        filtered_results.append(record)

    st.caption(f"نمایش {len(filtered_results)} سوال از مجموع {len(checklist_results)} سوال چک‌لیست")

    for record in filtered_results:
        meta = STATUS_META[record["status"]]
        short_question = (record["question_text"] or "بدون متن").strip()
        if len(short_question) > 80:
            short_question = short_question[:80] + "…"
        header = f":material/{EXPANDER_ICON[record['status']]}: {record['question_id']} — {short_question}"

        with st.expander(header):
            st.markdown(
                f"<span class='psa-status-badge' style='background:{meta['color']}'>"
                f"{icon(meta['icon'], 14)}{meta['label']}</span>",
                unsafe_allow_html=True,
            )
            st.write("")
            st.markdown(
                f"<div style='margin-bottom:.5rem;'>{icon('file-text', 15)} "
                f"<b>متن سوال:</b> {record['question_text'] or '—'}</div>",
                unsafe_allow_html=True,
            )
            if record["question_purpose"]:
                st.markdown(
                    f"<div style='margin-bottom:.5rem;'>{icon('target', 15)} "
                    f"<b>هدف بررسی:</b> {record['question_purpose']}</div>",
                    unsafe_allow_html=True,
                )
            if record["general_description"]:
                st.markdown(
                    f"<div style='margin-bottom:.5rem;'>{icon('info', 15)} "
                    f"<b>توضیحات تکمیلی:</b> {record['general_description']}</div>",
                    unsafe_allow_html=True,
                )

            if record["evaluation_condition"]:
                st.markdown(
                    f"<div style='margin:.7rem 0 .3rem 0;'>{icon('settings', 15)} <b>فرمول ارزیابی:</b></div>",
                    unsafe_allow_html=True,
                )
                st.code(record["evaluation_condition"], language="text")

            if record["condition_breakdown"]:
                st.markdown(
                    f"<div style='margin:.7rem 0 .3rem 0;'>{icon('list-checks', 15)} <b>ریز ارزیابی شرط‌ها:</b></div>",
                    unsafe_allow_html=True,
                )
                breakdown_rows = []
                for item in record["condition_breakdown"]:
                    res = item["result"]
                    label = BREAKDOWN_LABEL.get(res, "نامشخص")
                    breakdown_rows.append({"شرط": item["condition"], "نتیجه": label})
                st.dataframe(pd.DataFrame(breakdown_rows), width="stretch", hide_index=True)

            if record["extracted_data"]:
                st.markdown(
                    f"<div style='margin:.7rem 0 .3rem 0;'>{icon('database', 15)} <b>داده‌های استخراج‌شده:</b></div>",
                    unsafe_allow_html=True,
                )
                st.dataframe(pd.DataFrame(record["extracted_data"]), width="stretch", hide_index=True)

            if record["message"]:
                final_text = f"نتیجه نهایی: {record['message']}"
                if record["status"] == "TRUE":
                    st.success(final_text, icon=":material/check_circle:")
                elif record["status"] == "FALSE":
                    st.error(final_text, icon=":material/cancel:")
                elif record["status"] == "ERROR":
                    st.warning(final_text, icon=":material/warning:")
                else:
                    st.info(final_text, icon=":material/visibility:")


# --- تب داده‌های استخراج‌شده -------------------------------------------------
with tab_data:
    st.markdown(
        f'<div class="psa-section-title">{icon("database", 20)} مرور شیت‌های استخراج‌شده</div>',
        unsafe_allow_html=True,
    )
    flat_sheets = flatten_sheets_for_preview(result["imported_sheets"])
    if not flat_sheets:
        render_alert("info", "info", "هیچ داده‌ای برای نمایش وجود ندارد.")
    else:
        sheet_choice = st.selectbox(":material/table_view: انتخاب شیت برای پیش‌نمایش", sorted(flat_sheets.keys()))
        payload = flat_sheets[sheet_choice]
        if isinstance(payload, pd.DataFrame):
            st.dataframe(payload, width="stretch")
            st.caption(f"ابعاد داده: {payload.shape[0]} سطر × {payload.shape[1]} ستون")
        else:
            st.write(payload)


# --- تب خروجی گزارش ---------------------------------------------------------
with tab_export:
    st.markdown(
        f'<div class="psa-section-title">{icon("file-down", 20)} دانلود گزارش نهایی</div>',
        unsafe_allow_html=True,
    )
    st.caption("خروجی شامل خلاصه شاخص‌ها و جزئیات کامل تمام سوالات چک‌لیست حسابرسی است.")

    export_rows = []
    for record in checklist_results:
        export_rows.append(
            {
                "شناسه سوال": record["question_id"],
                "متن سوال": record["question_text"],
                "وضعیت": STATUS_META[record["status"]]["label"],
                "هدف بررسی": record["question_purpose"],
                "توضیحات تکمیلی": record["general_description"],
                "فرمول ارزیابی": record["evaluation_condition"] or "",
                "نتیجه نهایی": record["message"],
            }
        )
    export_df = pd.DataFrame(export_rows)
    summary_df = pd.DataFrame(
        [
            {
                "کل سوالات": summary["total"],
                "تطابق دارد": summary["true_count"],
                "عدم تطابق": summary["false_count"],
                "خطای پردازش": summary["error_count"],
                "نیازمند بررسی دستی": summary["manual_count"],
                "درصد تطابق": f"{summary['compliance_rate']:.1f}٪",
            }
        ]
    )

    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="خلاصه", index=False)
        export_df.to_excel(writer, sheet_name="چک لیست حسابرسی", index=False)

    dl_col1, dl_col2 = st.columns(2)
    with dl_col1:
        st.download_button(
            "دانلود گزارش اکسل",
            data=excel_buffer.getvalue(),
            file_name="گزارش_حسابرسی.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            icon=":material/download:",
            width="stretch",
        )
    with dl_col2:
        json_payload = json.dumps(
            {"summary": summary, "checklist": checklist_results}, ensure_ascii=False, indent=2
        ).encode("utf-8")
        st.download_button(
            "دانلود گزارش JSON",
            data=json_payload,
            file_name="گزارش_حسابرسی.json",
            mime="application/json",
            icon=":material/download:",
            width="stretch",
        )

    st.markdown(
        f"<div style='margin-top:1rem;'>{icon('list-checks', 15)} <b>پیش‌نمایش جدول چک‌لیست:</b></div>",
        unsafe_allow_html=True,
    )
    st.dataframe(export_df, width="stretch", hide_index=True)


st.markdown(
    f"""
    <div class="psa-footer">
        {icon('shield-check', 14)}
        داشبورد هوشمند حسابرسی و حسابداری فارسی —
        ساخته‌شده بر پایه‌ی گردش‌کاری main/main.ipynb و مستندات checklist_process.md
    </div>
    """,
    unsafe_allow_html=True,
)
