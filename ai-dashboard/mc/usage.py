"""Token usage riil dari `hermes insights` (parse output teks)."""
import re
import subprocess

_NUM_RE = {
    "sessions": r"Sessions:\s+([\d,]+)",
    "messages": r"Messages:\s+([\d,]+)",
    "input_tokens": r"Input tokens:\s+([\d,]+)",
    "output_tokens": r"Output tokens:\s+([\d,]+)",
    "total_tokens": r"Total tokens:\s+([\d,]+)",
}


def _num(s):
    return int(s.replace(",", ""))


def insights(profile, days=7, timeout=120):
    """Return dict metrik atau {'error': ...}."""
    try:
        proc = subprocess.run(
            ["hermes", "-p", profile, "insights", "--days", str(days)],
            capture_output=True, text=True, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return {"profile": profile, "error": str(e)[:200]}
    out = proc.stdout or ""
    data = {"profile": profile}
    for key, pat in _NUM_RE.items():
        m = re.search(pat, out)
        data[key] = _num(m.group(1)) if m else 0
    if proc.returncode != 0 and data["total_tokens"] == 0:
        data["error"] = (proc.stderr or "insights gagal")[:200]
    return data


def usage_all(profiles, days=7):
    total = {"profiles": [], "total_tokens": 0, "input_tokens": 0,
             "output_tokens": 0, "sessions": 0, "messages": 0}
    for p in profiles:
        d = insights(p, days=days)
        total["profiles"].append(d)
        for k in ("total_tokens", "input_tokens", "output_tokens", "sessions", "messages"):
            total[k] += d.get(k, 0)
    total["profiles"].sort(key=lambda d: d.get("total_tokens", 0), reverse=True)
    return total
