"""Measures a page with Google PageSpeed Insights: speed, accessibility, good practices and SEO, from 0 to 100.

It is Google's own Lighthouse test, run on Google's computers. It is free. Without a key Google
usually answers "too many requests", so a free key goes in .env as PAGESPEED_API_KEY.
"""

import os
from concurrent.futures import ThreadPoolExecutor

import httpx

PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
# Lighthouse category -> the name we use in the report.
CATEGORIES = {"performance": "speed", "accessibility": "accessibility", "best-practices": "best_practices", "seo": "seo"}
# Lighthouse audit -> the name we use in the report.
METRICS = {
    "first-contentful-paint": "first_content",
    "largest-contentful-paint": "main_content",
    "total-blocking-time": "blocked_time",
    "cumulative-layout-shift": "layout_shift",
    "speed-index": "speed_index",
}
NO_KEY_HINT = "Google no quiso medir (demasiadas peticiones sin clave). Con una clave gratuita de PageSpeed en .env no pasa."


def has_key() -> bool:
    return bool(os.environ.get("PAGESPEED_API_KEY", "").strip())


def measure(url: str, strategy: str) -> dict:
    """Run one test. strategy is "mobile" or "desktop". It never raises: errors come back in the result."""
    failed = {"ok": False, "strategy": strategy}
    params = [("url", url), ("strategy", strategy), ("locale", "es")] + [("category", name) for name in CATEGORIES]
    key = os.environ.get("PAGESPEED_API_KEY", "").strip()
    if key:
        params.append(("key", key))
    try:
        response = httpx.get(PSI_URL, params=params, timeout=120)
    except httpx.HTTPError as error:
        return failed | {"error": f"No pude hablar con Google PageSpeed ({type(error).__name__})."}
    if response.status_code == 429:
        return failed | {"error": NO_KEY_HINT}
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code != 200:
        message = (data.get("error") or {}).get("message", "") if isinstance(data, dict) else ""
        return failed | {"error": f"Google PageSpeed respondió con un error {response.status_code}. {message[:200]}".strip()}

    lighthouse = data.get("lighthouseResult", {})
    audits = lighthouse.get("audits", {})
    scores, problems = {}, {}
    for category_id, name in CATEGORIES.items():
        category = lighthouse.get("categories", {}).get(category_id, {})
        score = category.get("score")
        scores[name] = round(score * 100) if score is not None else None
        # The audits this page fails, heaviest first.
        failing = []
        for reference in category.get("auditRefs", []):
            audit = audits.get(reference.get("id"), {})
            if reference.get("id") in METRICS or audit.get("score") is None or audit["score"] >= 0.9:
                continue
            if audit.get("scoreDisplayMode") in ("notApplicable", "manual", "informative"):
                continue
            value = (audit.get("displayValue") or "").replace("\xa0", " ")
            failing.append((-reference.get("weight", 0), audit["score"],
                            audit.get("title", "") + (f" ({value})" if value else "")))
        problems[name] = [title for _, _, title in sorted(failing)[:8]]
    if scores["speed"] is None:
        return failed | {"error": "Google PageSpeed no pudo cargar la página."}
    return {
        "ok": True, "strategy": strategy, "scores": scores, "problems": problems,
        "metrics": {name: (audits.get(audit_id, {}).get("displayValue") or "").replace("\xa0", " ")
                    for audit_id, name in METRICS.items()},
        # The same measurements as numbers (milliseconds; the layout shift has no unit), for rules to compare.
        "numbers": {name: audits.get(audit_id, {}).get("numericValue") for audit_id, name in METRICS.items()},
        # FAST, AVERAGE or SLOW according to real visitors, when Google has enough of them.
        "real_visitors": data.get("loadingExperience", {}).get("overall_category", ""),
    }


def measure_both(url: str) -> dict:
    """Mobile and desktop at the same time: each test takes Google half a minute or so."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        mobile, desktop = pool.map(lambda strategy: measure(url, strategy), ("mobile", "desktop"))
    return {"mobile": mobile, "desktop": desktop}


def describe(results: dict) -> str:
    """The measurements as plain text for the model."""
    lines = ["PAGESPEED (Google Lighthouse, measured on the homepage; scores go from 0 to 100)"]
    for strategy in ("mobile", "desktop"):
        result = results.get(strategy) or {}
        if not result.get("ok"):
            lines.append(f"{strategy}: NOT MEASURED. {result.get('error', '')}")
            continue
        scores, metrics = result["scores"], result["metrics"]
        lines.append(f"{strategy}: speed {scores['speed']}, accessibility {scores['accessibility']},"
                     f" good practices {scores['best_practices']}, SEO {scores['seo']}")
        lines.append(f"  first content {metrics['first_content']}, main content {metrics['main_content']},"
                     f" blocked time {metrics['blocked_time']}, layout shift {metrics['layout_shift']}"
                     + (f"; real visitors: {result['real_visitors']}" if result["real_visitors"] else ""))
        for name, label in (("speed", "speed"), ("accessibility", "accessibility"), ("seo", "SEO"),
                            ("best_practices", "good practices")):
            if result["problems"][name]:
                lines.append(f"  {label} problems: " + "; ".join(result["problems"][name]))
    return "\n".join(lines)
