# config.py
from typing import Dict, List, Any

# Target Excel File Path (using raw string to avoid backslash escaping issues)
EXCEL_FILE_PATH: str = r"extraction scipts\Budget.xlsx"

# Dictionary representing form properties for parsing
FORMS_PARAM: Dict[str, Dict[str, Any]] = {
    'form1': {
        'header_rows': [3, 4], 
        'start_data_row': 5, 
        'end_data_row': 22, 
        'start_metadata_row': 23, 
        'end_metadata_row': -1, 
        'hierarchy_cols_indices': [14, 13, 12]
    },
    'form2': {
        'header_rows': [2, 3], 
        'start_data_row': 4, 
        'end_data_row': 17, 
        'start_metadata_row': 18, 
        'end_metadata_row': -1,
        'hierarchy_cols_indices': [14, 13]
    },
    'form3': {
        'header_rows': [], 
        'start_data_row': -1, 
        'end_data_row': -1, 
        'start_metadata_row': 3, 
        'end_metadata_row': -1,
        'hierarchy_cols_indices': []
    },
    'form4': {
        'header_rows': [6, 7, 8], 
        'start_data_row': 9, 
        'end_data_row': 28, 
        'start_metadata_row': 29, 
        'end_metadata_row': -1,
        'hierarchy_cols_indices': range(20, 18-1, -1)
    },
    
    'form5': {
        'header_rows': [3, 4], 
        'start_data_row': 5, 
        'end_data_row': 90, 
        'start_metadata_row': 91, 
        'end_metadata_row': -1,
        'hierarchy_cols_indices': range(14, 11-1, -1)
    },
    'form5-1a': {
        'header_rows': [3, 4], 
        'start_data_row': 5, 
        'end_data_row': 16, 
        'start_metadata_row': -1, 
        'end_metadata_row': -1,
        'hierarchy_cols_indices': range(13, 10-1, -1)
    },
    'form5-1b': {
        'header_rows': [3, 4], 
        'start_data_row': 21, 
        'end_data_row': 23, 
        'start_metadata_row': 24, 
        'end_metadata_row': -1,
        'hierarchy_cols_indices': range(13, 10-1, -1)
    },
    'form6a': {
        'header_rows': [4, 5], 
        'start_data_row': 6, 
        'end_data_row': 15, 
        'start_metadata_row': -1, 
        'end_metadata_row': -1,
        'hierarchy_cols_indices': range(12, 11-1, -1)
        # 'hierarchy_cols_indices': range(11, 10-1, -1)       # rust engine
    },
    'form6b': {
        'header_rows': [17, 18], 
        'start_data_row': 19, 
        'end_data_row': 27, 
        'start_metadata_row': 28, 
        'end_metadata_row': -1,
        'hierarchy_cols_indices': range(12, 11-1, -1)
        # 'hierarchy_cols_indices': range(11, 10-1, -1)       # rust engine
    },
    'form7': {
        'header_rows': [4, 5], 
        'start_data_row': 6, 
        'end_data_row': 23, 
        'start_metadata_row': 24, 
        'end_metadata_row': -1,
        'hierarchy_cols_indices': range(14, 13-1, -1)
    },
    'form8': {
        'header_rows': [4, 5], 
        'start_data_row': 6, 
        'end_data_row': 25, 
        'start_metadata_row': 26, 
        'end_metadata_row': -1,
        'hierarchy_cols_indices': range(10, 7-1, -1)
    },
    'form9': {
        'header_rows': [4, 5], 
        'start_data_row': 6, 
        'end_data_row': 63, 
        'start_metadata_row': 64, 
        'end_metadata_row': -1,
        'hierarchy_cols_indices': range(7, 6-1, -1)
    },
    'form10': {
        'header_rows': [4, 5], 
        'start_data_row': 6, 
        'end_data_row': 20, 
        'start_metadata_row': 21, 
        'end_metadata_row': -1,
        'hierarchy_cols_indices': [7]
    },
}

# Map normalized Persian sheet names to English config keys
SHEET_TO_CONFIG_MAP: Dict[str, List[str]] = {
    "فرم 1": ["form1"],
    "فرم 2": ["form2"],
    "فرم 3": ["form3"],
    "فرم 4": ["form4"],
    "فرم 5": ["form5"],
    "فرم 1-5": ["form5-1a", "form5-1b"],
    "فرم1-5": ["form5-1a", "form5-1b"], 
    "فرم 6": ["form6a", "form6b"],
    "فرم 7": ["form7"],
    "فرم 8": ["form8"],
    "فرم 9": ["form9"],
    "فرم 10": ["form10"]
}