from normalize_persian import normalize_persian_text, normalize_dataframe

import re
import logging
from rapidfuzz import fuzz, process

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("Search in Metadata")


def determine_data_format(raw_value):
    """
    Analyzes the extracted string and converts it to the appropriate data type.
    Returns a tuple: (converted_value, detected_format_type)
    """
    if not raw_value:
        return None, "unknown"
        
    # Remove commas which are commonly used in financial numbers
    clean_value = raw_value.replace(",", "")
    
    # Check for Integer
    if clean_value.lstrip('-').isdigit():
        return int(clean_value), "integer"
        
    # Check for Float
    try:
        val = float(clean_value)
        return val, "float"
    except ValueError:
        pass
        
    # Fallback to String
    return raw_value, "string"


def extract_value_after_keyword(segment: str, target_keyword: str):
    """
    Dynamically finds the target keyword (e.g., "مبلغ") in a text segment 
    and extracts the immediately following token. 
    """
    # A simple dynamic regex just to find the next contiguous non-space string
    # ignoring colons or dashes immediately after the keyword.
    pattern = re.compile(rf"{target_keyword}\s*[:\-]*\s*([^\s]+)", re.IGNORECASE)
    match = pattern.search(segment)
    
    if match:
        return match.group(1).strip()
    return None


def search_in_text(text: str, anchor_phrase: str, target_keyword: str, fuzzy_cutoff=75, bag_cutoff=0.6):
    """
    Generalized extractor using RapidFuzz and Token Similarity (Bag of Words).
    Robust against structural changes, typos, and specific year mentions.
    """
    normalized_text = normalize_persian_text(text)
    normalized_anchor = normalize_persian_text(anchor_phrase)
    
    # Remove digits from anchor to make it strictly year-agnostic
    anchor_no_digits = re.sub(r'\d+', '', normalized_anchor).strip()
    anchor_tokens = set(anchor_no_digits.split())
    
    # Split text into logical segments (sentences/paragraphs)
    segments = [seg.strip() for seg in re.split(r'[\n\.]', normalized_text) if len(seg.strip()) > 10]
    
    val_method_1 = None
    val_method_2 = None
    fmt_method_1 = None
    fmt_method_2 = None
    
    # ==========================================
    # Method 1: Fuzzy Matching (token_set_ratio)
    # Excellent for out-of-order words and typos
    # ==========================================
    if segments:
        # token_set_ratio ignores word order and duplicates
        best_match, score, _ = process.extractOne(
            anchor_no_digits, 
            segments, 
            scorer=fuzz.token_set_ratio
        )
        
        if score >= fuzzy_cutoff:  # Threshold for fuzzy matching
            raw_val_1 = extract_value_after_keyword(best_match, target_keyword)
            if raw_val_1:
                val_method_1, fmt_method_1 = determine_data_format(raw_val_1)
                logger.info(f"Method 1 (Fuzzy) extracted: {val_method_1} (Format: {fmt_method_1}, Score: {score})")
        else:
            logger.info(f"Method 1 (Fuzzy) failed. Best score {score} below threshold.")

    # ==========================================
    # Method 2: Token Intersection (Bag of Words)
    # Excellent for heavy structural changes or injected texts
    # ==========================================
    best_segment_m2 = None
    max_intersection_ratio = 0.0
    
    for seg in segments:
        seg_no_digits = re.sub(r'\d+', '', seg)
        seg_tokens = set(seg_no_digits.split())
        
        # Calculate what percentage of anchor words exist in this segment
        if not anchor_tokens:
            continue
            
        intersection = len(anchor_tokens.intersection(seg_tokens))
        ratio = intersection / len(anchor_tokens)
        
        if ratio > max_intersection_ratio:
            max_intersection_ratio = ratio
            best_segment_m2 = seg
            
    if max_intersection_ratio >= bag_cutoff: # At least 60% of keywords must be present
        raw_val_2 = extract_value_after_keyword(best_segment_m2, target_keyword)
        if raw_val_2:
            val_method_2, fmt_method_2 = determine_data_format(raw_val_2)
            logger.info(f"Method 2 (Token Math) extracted: {val_method_2} (Format: {fmt_method_2}, Ratio: {max_intersection_ratio:.2f})")
    else:
        logger.info(f"Method 2 (Token Math) failed. Best ratio {max_intersection_ratio:.2f} below threshold.")

    # ==========================================
    # Validation, Comparison, and Format Resolution Logic
    # ==========================================
    final_result = {
        "value": None,
        "format": "unknown",
        "method_used": "none"
    }

    if val_method_1 is not None and val_method_2 is not None:
        if val_method_1 == val_method_2:
            logger.info(f"Both methods agreed on value: {val_method_1}. Successful extraction.")
            final_result = {"value": val_method_1, "format": fmt_method_1, "method_used": "both_agreed"}
        else:
            logger.warning(f"Conflict! Method 1: {val_method_1} vs Method 2: {val_method_2}. Returning None to prevent false data.")
            # Conflict results in None for safety
            
    elif val_method_1 is not None:
        logger.info("Only Method 1 (Fuzzy) succeeded.")
        final_result = {"value": val_method_1, "format": fmt_method_1, "method_used": "fuzzy_only"}
        
    elif val_method_2 is not None:
        logger.info("Only Method 2 (Token Math) succeeded.")
        final_result = {"value": val_method_2, "format": fmt_method_2, "method_used": "token_math_only"}
        
    else:
        logger.warning("Both extraction methods failed. Target data not found.")

    return final_result


# # --- Test Case ---
# if __name__ == "__main__":
#     raw_text = """بودجه تفصیلی به استناد ابلاغیه شماره 14193 مورخ 98/01/21 سازمان برنامه و بودجه کشور و دستور دوم هیات امنای منطقه دو فناوری مورخ 30/98/0x مبادله می شود. اعتبارات هزینه ای، تملک دارایی های سرمایه ای و درآمد اختصاصی به ترتیب با مبالغ 145500 و x‏090 و 38800 میلیون ریال مصوب گردیده است.
#     افزایش اعتبارات تملک دارایی های سرمایه ای : طبق موافقتنامه تملک به شماره 352371 مورخ 98/05/30 به منظور افزایش اعتبار طرح تعمیرات اساسی و خرید تجهیزات به مبلغ 13500 م.ریال از محل اعتبارات ردیف قانون استفاده متوازن از امکانات کشور (جز18 ردیف 550000) صادر شده است.
#     مجوز افزایش درآمد اختصاصی : بر اساس تاییدیه شماره x/x‏3/350489 مورخ 98/12/25 درآمد اختصاصی سال 98 به مبلغ 1740x میلیون ریال افزایش یافت.
#     مجوز جابجایی از اختصاصی 98 به تملک 98: بر اساس دستور شماره 7 از صورتجلسه کمیسیون دائمی مورخ 29-11-98 با جابجایی مبلغ 8300 میلیون ریال از اعتبارات اختصاصی 98 به تملک دارائی های سرمایه ای 98 موافقت بعمل آمد. باتوجه به اینکه در جداول بودجه تفصیلی ستونی برای جابجایی از سال جاری وجود ندارد اعتبار مربوطه از افزایش درآمد اختصاصی کسر و به ستون تملک منتقل شد.
#     """
#     # Notice how we drop the year completely in the anchor
#     # anchor = "مجوز جابجایی از اختصاصی به تملک"
#     anchor = "افزایش اعتبار طرح تعمیرات اساسی و خرید تجهیزات"
#     target_keyword = "مبلغ"
    
#     result = extract_data_point_generalized(raw_text, anchor, target_keyword)
#     print("Final Extracted Data:", result)