"""Single-user support.

The app has no login screen. This middleware pins every request to one owner
account so views can keep using `request.user` exactly as they would in a
multi-user app -- every model carries a `user` FK already, so growing into
real auth later is just: add a login, delete this middleware.
"""

from django.conf import settings
from django.contrib.auth import get_user_model


def get_owner():
    """Return (creating if needed) the single account this app belongs to."""
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=settings.PLANNER_OWNER_USERNAME,
        defaults={"is_staff": True, "is_superuser": True},
    )
    return user


class AutoUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # The admin keeps real session auth so it stays usable/lockable.
        if not request.path.startswith("/admin/"):
            request.user = get_owner()
        return self.get_response(request)
