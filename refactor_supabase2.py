import re

with open("src/database.py", "r") as f:
    content = f.read()

# Remove remaining USE_SUPABASE blocks
content = re.sub(r'\s+# -- Supabase --\n\s+if USE_SUPABASE.*?:\n(?:(?:\s{8}.*?\n)+)', '\n', content)
content = re.sub(r'^\s*if USE_SUPABASE and _SUPABASE_AVAILABLE:.*?\n(?:(?:\s+.*?\n)+?)(?=\s*# -- SQLite --|\s*return|\s*except)', '\n', content, flags=re.MULTILINE|re.DOTALL)
content = re.sub(r'  2\. Supabase    — if USE_SUPABASE=true and SUPABASE_URL \+ SUPABASE_KEY are set\n', '', content)

with open("src/database.py", "w") as f:
    f.write(content)

with open("src/users.py", "r") as f:
    content = f.read()

content = re.sub(r', USE_SUPABASE', '', content)
content = re.sub(r'get_supabase, ', '', content)
content = re.sub(r'\s+# -- Supabase --\n\s+if USE_SUPABASE:\n(?:(?:\s+.*?\n)+?)(?=\s+# -- SQLite --)', '\n', content)
content = content.replace("SQLite/Supabase", "SQLite")

with open("src/users.py", "w") as f:
    f.write(content)

