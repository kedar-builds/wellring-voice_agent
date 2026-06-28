import os
import json

users_file = "src/data/users.json"
if os.path.exists(users_file):
    with open(users_file, "r") as f:
        data = json.load(f)
        print(json.dumps(data, indent=2))
else:
    print("No users.json found")
