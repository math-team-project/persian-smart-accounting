# to_search_in_metadata Module Documentation

## Overview
This module provides robust, year-agnostic text extraction and pattern matching capabilities for unstructured metadata. It leverages fuzzy matching and token-based similarity to locate target data points without relying on rigid regular expressions.

---

## Functions

### 1. `normalize_persian_text(text: str) -> str`
* **Description:** Standardizes Persian and Arabic text by unifying character variants, normalizing spacing around common punctuation, and converting Persian and Arabic numerals into standard English digits.

### 2. `determine_data_format(raw_value: str) -> tuple`
* **Description:** Analyzes an extracted raw string value, cleans formatting characters such as commas, and determines its appropriate data type (`integer`, `float`, or `string`). Returns a tuple containing the converted value and its detected format type.

### 3. `extract_value_after_keyword(segment: str, target_keyword: str) -> str`
* **Description:** Dynamically searches a text segment for a specific target keyword (e.g., "مبلغ") and extracts the immediately following token or phrase while ignoring trailing separators like colons or dashes.

### 4. `search_in_text(text: str, anchor_phrase: str, target_keyword: str = "مبلغ") -> dict`
* **Description:** Serves as the primary extraction engine. It splits the input text into logical segments, strips out specific year digits to maintain structural flexibility, and employs dual-method evaluation (Fuzzy matching and Token Intersection). It compares the results from both methods and returns a structured dictionary containing the validated value, its format, and the execution method used.# to_search_in_metadata Module Documentation

## Overview
This module provides robust, year-agnostic text extraction and pattern matching capabilities for unstructured metadata. It leverages fuzzy matching and token-based similarity to locate target data points without relying on rigid regular expressions.

---

## Functions

### 1. `normalize_persian_text(text: str) -> str`
* **Description:** Standardizes Persian and Arabic text by unifying character variants, normalizing spacing around common punctuation, and converting Persian and Arabic numerals into standard English digits.

### 2. `determine_data_format(raw_value: str) -> tuple`
* **Description:** Analyzes an extracted raw string value, cleans formatting characters such as commas, and determines its appropriate data type (`integer`, `float`, or `string`). Returns a tuple containing the converted value and its detected format type.

### 3. `extract_value_after_keyword(segment: str, target_keyword: str) -> str`
* **Description:** Dynamically searches a text segment for a specific target keyword (e.g., "مبلغ") and extracts the immediately following token or phrase while ignoring trailing separators like colons or dashes.

### 4. `search_in_text(text: str, anchor_phrase: str, target_keyword: str = "مبلغ") -> dict`
* **Description:** Serves as the primary extraction engine. It splits the input text into logical segments, strips out specific year digits to maintain structural flexibility, and employs dual-method evaluation (Fuzzy matching and Token Intersection). It compares the results from both methods and returns a structured dictionary containing the validated value, its format, and the execution method used.