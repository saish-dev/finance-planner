"""Cache-busting for the app's own CSS and JS.

`{% asset 'planner.css' %}` is the normal static URL plus `?v=<modified time>`,
so a browser never keeps serving an old stylesheet or script after the file
has changed. Files that cannot be found fall back to the plain URL.
"""

import os

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@register.simple_tag
def asset(path):
    url = static(path)
    found = finders.find(path)
    if isinstance(found, (list, tuple)):
        found = found[0] if found else None
    if found:
        try:
            return f"{url}?v={int(os.path.getmtime(found))}"
        except OSError:
            pass
    return url
