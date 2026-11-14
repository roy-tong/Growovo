"""Growovo local patch: reuse one automation tab across read/write commands."""
def automation_page(context):
    pages = list(context.pages)
    for page in pages:
        if page.url.startswith(('https://x.com/', 'https://twitter.com/')):
            return page
    for page in pages:
        if page.url in ('about:blank', 'chrome://newtab/', 'chrome://newtab'):
            return page
    return context.new_page()


def release_page(page):
    # Keep the shared page for the next operation. Exiting Playwright disconnects.
    return None


def connect_browser(browser_type, endpoint):
    """Preserve the existing browser context using Playwright's native option."""
    import inspect
    kwargs = {}
    if "no_defaults" in inspect.signature(browser_type.connect_over_cdp).parameters:
        kwargs["no_defaults"] = True
    return browser_type.connect_over_cdp(endpoint, **kwargs)
