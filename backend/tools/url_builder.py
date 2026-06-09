"""
Build pre-filled deep-link search URLs for each platform using intent params.
Falls back to platform homepage if template params aren't available.
"""
import re
from urllib.parse import quote_plus


def build_search_url(platform_config: dict, intent_params: dict) -> str:
    """
    Fill platform's search_url_template with intent params.
    Returns homepage (website) if template has unfilled placeholders.
    """
    template = platform_config.get("search_url_template", "")
    website  = platform_config.get("website", "")

    if not template:
        return website

    # Flatten params — handle nested dicts like budget: {max: 5000}
    flat: dict[str, str] = {}
    for k, v in intent_params.items():
        if isinstance(v, dict):
            flat[k] = str(list(v.values())[0]) if v else ""
        else:
            flat[k] = quote_plus(str(v)) if v is not None else ""

    # Common aliases
    flat.setdefault("check_in",   flat.get("checkin", flat.get("date", "")))
    flat.setdefault("check_out",  flat.get("checkout", flat.get("return_date", "")))
    flat.setdefault("event_type", flat.get("query", flat.get("type", "")))
    flat.setdefault("product_name", flat.get("query", ""))
    flat.setdefault("cuisine",    flat.get("query", ""))

    try:
        filled = template.format_map(flat)
        # If any {placeholder} survived → template has params we don't have
        if re.search(r"\{[^}]+\}", filled):
            return website
        return filled
    except Exception:
        return website
