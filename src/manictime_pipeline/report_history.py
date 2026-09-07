"""Compact monthly history files, read on demand by the offline history page."""

from __future__ import annotations

import json


def pack_history(rows, source_file):
    """Dictionary-encode repeated strings without losing original titles or IDs.

    Rows: local start/end, text, application, activity ID, Active seconds,
    kind (title/search), URL, timeline ID. A chunk belongs to one source CSV.
    """
    strings, ids = [], {}
    packed = []
    for row in rows:
        item = list(row)
        for col in (2, 3, 7):
            value = item[col]
            if value not in ids:
                ids[value] = len(strings)
                strings.append(value)
            item[col] = ids[value]
        packed.append(item)
    data = {"strings": strings, "rows": packed, "source_file": source_file}
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = (
        payload.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    )
    return ("window.__historyChunk=" + payload + ";").encode("utf-8")
