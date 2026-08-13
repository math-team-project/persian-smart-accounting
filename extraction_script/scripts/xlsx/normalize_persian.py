import re
import numpy as np
import pandas as pd


def normalize_persian_text(text):
    """
    Normalizes Persian text by unifying characters like 'ی' and 'ک',
    removing zero-width non-joiners, and trimming spaces.
    """
    if not isinstance(text, str):
        return text

    replacements = {
        'ي': 'ی',
        'ك': 'ک',
        'ؤ': 'و',
        'إ': 'ا',
        'أ': 'ا',
        'آ': 'آ',
        'ة': 'ه',
        'ۀ': 'هٔ',
        '۰': '0',
        '۱': '1',
        '۲': '2',
        '۳': '3',
        '۴': '4',
        '۵': '5',
        '۶': '6',
        '۷': '7',
        '۸': '8',
        '۹': '9',
        '٠': '0',       # Arabic-Indic digits to English digits
        '١': '1',
        '٢': '2',
        '۳': '3',
        '٤': '4',
        '٥': '5',
        '٦': '6',
        '٧': '7',
        '٨': '8',
        '٩': '9',
        '‌': ' ',       # Zero-width non-joiner to regular space
        '\u200c': ' ',  # Alternative zero-width non-joiner hex
        '\u200f': '',   # Right-to-left mark removal
        '\u200e': '',   # Left-to-right mark removal
        '\n': ' ',      # Newlines to space
        '\r': '',       # Carriage returns removal
        '\t': ' '       # Tabs to space
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    # Remove multiple spaces
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def normalize_dataframe(df):
    """
    Applies the Persian normalizer element-wise to every single cell
    and column name in the DataFrame, ensuring full dataset normalization.
    Uses .map() (compatible with modern pandas versions, replacing deprecated applymap).
    """
    # Normalize column names
    df.columns = [normalize_persian_text(col) for col in df.columns]

    # Normalize every cell value element-wise across the entire dataframe
    return df.map(lambda x: normalize_persian_text(x) if isinstance(x, str) else x)


if __name__ == "__main__":
    pass
