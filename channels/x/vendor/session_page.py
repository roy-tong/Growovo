import os


def automation_page(context, url_prefixes=None):
    pages = list(context.pages)
    managed_url = os.environ.get("MKT_MANAGED_TAB_URL", "")
    if managed_url:
        for page in pages:
            if page.url == managed_url or page.url.startswith(managed_url.rstrip("/") + "/"):
                return page
        raise RuntimeError(
            "managed_tab_required: no x.com/home tab in this CDP browser; "
            "open x.com/home in the debug browser or use browser-attach")
    prefixes = tuple(url_prefixes or ("https://x.com/", "https://twitter.com/"))
    for page in pages:
        if page.url.startswith(prefixes):
            return page
    for page in pages:
        if page.url in ("about:blank", "chrome://newtab/", "chrome://newtab"):
            return page
    raise RuntimeError("managed_tab_required: reuse a WebMonitor managed tab; Playwright new_page is disabled")


def release_page(page):
    return None


def connect_browser(browser_type, endpoint):
    """Preserve the existing browser context using Playwright's native option."""
    import inspect
    kwargs = {}
    if "no_defaults" in inspect.signature(browser_type.connect_over_cdp).parameters:
        kwargs["no_defaults"] = True
    return browser_type.connect_over_cdp(endpoint, **kwargs)
