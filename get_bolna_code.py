with open("src/main.py", "r") as f:
    lines = f.readlines()
    start = 0
    end = 0
    for i, line in enumerate(lines):
        if "async def _do_bolna_call" in line:
            start = i
        if start > 0 and i > start + 30 and "def " in line and line.startswith("def "):
            end = i
            break
    print("".join(lines[start:start+50]))
