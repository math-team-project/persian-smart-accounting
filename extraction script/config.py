# config.py
from typing import Dict, List, Any

# Target Excel File Path (using raw string to avoid backslash escaping issues)
EXCEL_FILE_PATH: dict = {'تفضیلی': r"extraction script\Budget.xlsx",
                         'اصلاحیه': r"extraction script\RevisedBudget.xlsx.xlsx"}

# Dictionary representing form properties for parsing
FORMS_PARAM: Dict[str, Dict[List[int], Any]] = {
    'form1': {
        'header_rows': [3, 4], 
        'data_row': [5,22], 
        'discription_row': [23,-1],
        'form_header' : [1,2+1],
        'hierarchy_cols_indices': [14, 13, 12]
    },
    'form2': {
        'header_rows': [2, 3], 
        'data_row': [4,17], 
        'discription_row': [18,-1],
        'form_header' : [0,1+1],
        'hierarchy_cols_indices': [14, 13]
    },
    'form3': {
        'header_rows': [], 
        'data_row': [-1,-1], 
        'discription_row': [3,-1],
        'form_header' : [1,3+1],
        'hierarchy_cols_indices': []
    },
    'form4': {
        'header_rows': [6, 7, 8], 
        'data_row': [9,28], 
        'discription_row': [29,-1],
        'form_header' : [3,4+1],
        'hierarchy_cols_indices': range(20, 18-1, -1)
    },
    
    'form5': {
        'header_rows': [3, 4], 
        'data_row': [5,90], 
        'discription_row': [91,-1],
        'form_header' : [1,2+1],
        'hierarchy_cols_indices': range(14, 11-1, -1)
    },
    'form5-1a': {
        'header_rows': [3, 4], 
        'data_row': [5,16], 
        'discription_row': [-1,-1],
        'form_header' : [1,2+1],
        'hierarchy_cols_indices': range(13, 10-1, -1)
    },
    'form5-1b': {
        'header_rows': [3, 4], 
        'data_row': [21,23], 
        'discription_row': [24,-1],
        'form_header' : [17,18+1],
        'hierarchy_cols_indices': range(13, 10-1, -1)
    },
    'form6a': {
        'header_rows': [4, 5], 
        'data_row': [6,15], 
        'discription_row': [-1,-1],
        'form_header' : [1,3+1],
        'hierarchy_cols_indices': range(12, 11-1, -1)
        # 'hierarchy_cols_indices': range(11, 10-1, -1)       # rust engine
    },
    'form6b': {
        'header_rows': [17, 18], 
        'data_row': [19,27], 
        'discription_row': [28,-1],
        'form_header' : [16,16+1],
        'hierarchy_cols_indices': range(12, 11-1, -1)
        # 'hierarchy_cols_indices': range(11, 10-1, -1)       # rust engine
    },
    'form7': {
        'header_rows': [4, 5], 
        'data_row': [6,23], 
        'discription_row': [24,-1],
        'form_header' : [1,2+1],
        'hierarchy_cols_indices': range(14, 13-1, -1)
    },
    'form8': {
        'header_rows': [4, 5], 
        'data_row': [6,25], 
        'discription_row': [26,-1],
        'form_header' : [1,3+1],
        'hierarchy_cols_indices': range(10, 7-1, -1)
    },
    'form9': {
        'header_rows': [4, 5], 
        'data_row': [6,63], 
        'discription_row': [64,-1],
        'form_header' : [1,2+1],
        'hierarchy_cols_indices': range(7, 6-1, -1)
    },
    'form10': {
        'header_rows': [4, 5], 
        'data_row': [6,20], 
        'discription_row': [21,-1],
        'form_header' : [1,2+1],
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