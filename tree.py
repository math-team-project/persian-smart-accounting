from directory_tree import display_tree

# درخت پوشه‌ی جاری
display_tree("rag_chat_module",  max_depth=3)

# درخت یک پوشه‌ی مشخص
# display_tree("extraction_script")

# # فقط تا عمق ۲ سطح
# display_tree("extraction_script", max_depth=2)

# # گرفتن خروجی به‌صورت رشته (به‌جای چاپ)
# tree_str = display_tree("extraction_script", string_rep=True)
# print(tree_str)