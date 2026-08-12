import re

with open("src/database.py", "r") as f:
    content = f.read()

# Remove Supabase vars
content = re.sub(r'USE_SUPABASE: bool = os\.environ\.get\("USE_SUPABASE", "false"\)\.lower\(\) == "true"\n', '', content)
content = re.sub(r'SUPABASE_URL: str = os\.environ\.get\("SUPABASE_URL", ""\)\n', '', content)
content = re.sub(r'SUPABASE_KEY: str = os\.environ\.get\("SUPABASE_KEY", ""\)\n', '', content)

# Remove the import block
content = re.sub(r'try:\n\s+from supabase import create_client, Client as SupabaseClient\n\s+_SUPABASE_AVAILABLE = True\nexcept ImportError:\n\s+SupabaseClient = Any\s+# type: ignore\[misc,assignment\]\n\s+_SUPABASE_AVAILABLE = False\n', '', content)

# Function to remove a whole function definition by name
def remove_func(name, text):
    pattern = r'def ' + name + r'\(.*?\)(?: -> .*?)?:\n(?:(?:\s+.*?\n)+(?:\s*\n)*)'
    return re.sub(pattern, '', text, count=1)

content = remove_func('get_supabase', content)
content = remove_func('_log_interaction_supabase', content)
content = remove_func('_symptom_count_supabase', content)
content = remove_func('_get_assessments_supabase', content)
content = remove_func('_get_assessment_stats_supabase', content)

# Now remove the if USE_SUPABASE blocks
content = re.sub(r'\s+# -- Supabase --\n\s+if USE_SUPABASE.*?:(.*?)(?=\n\s+(?:# -- SQLite --|logger\.error|return|if _use_postgres|except))', '', content, flags=re.DOTALL)

with open("src/database.py", "w") as f:
    f.write(content)

