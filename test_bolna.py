def _find_key(data, target_key):
    if isinstance(data, dict):
        if target_key in data:
            return data[target_key]
        for k, v in data.items():
            res = _find_key(v, target_key)
            if res:
                return res
    elif isinstance(data, list):
        for item in data:
            res = _find_key(item, target_key)
            if res:
                return res
    return None

payload = {"data": {"call_status": "no_answer", "recipient_phone_number": "+918421971145"}}
print(_find_key(payload, "call_status"))
print(_find_key(payload, "recipient_phone_number"))
