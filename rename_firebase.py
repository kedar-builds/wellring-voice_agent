import os
import glob

def replace_in_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    
    if 'clerk_id' in content or 'CLERK_ID' in content:
        content = content.replace('clerk_id', 'clerk_id')
        content = content.replace('CLERK_ID', 'CLERK_ID')
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"Updated {filepath}")

for root, _, files in os.walk('.'):
    if 'node_modules' in root or 'venv' in root or '.git' in root or '__pycache__' in root:
        continue
    for file in files:
        if file.endswith('.py') or file.endswith('.md') or file.endswith('.sql'):
            replace_in_file(os.path.join(root, file))

