import sys
path = "../WellRing-frontend/src/context/AppContext.jsx"
with open(path, "r") as f:
    content = f.read()

old_content = """  useEffect(() => {
    if (elderProfile?.phone) {
      fetchCallTimeline(elderProfile.phone);
    }
  }, [elderProfile?.phone, fetchCallTimeline]);"""

new_content = """  useEffect(() => {
    if (elderProfile?.phone) {
      (async () => { await fetchCallTimeline(elderProfile.phone); })();
    }
  }, [elderProfile?.phone, fetchCallTimeline]);"""

if old_content in content:
    with open(path, "w") as f:
        f.write(content.replace(old_content, new_content))
    print("Patched successfully")
else:
    print("Could not find content to patch")
