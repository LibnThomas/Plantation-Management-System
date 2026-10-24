"""Login check for the number of users the subscription plan allows."""

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.forms import AuthenticationForm

from . import appsettings, licence
from .i18n import ui


def plan_users(user_model=None):
    """Active users who count against the plan (administrators are not counted), oldest first."""
    user_model = user_model or get_user_model()
    return user_model.objects.filter(is_active=True, is_superuser=False).order_by("date_joined", "id")


class PlanUserLimitBackend(ModelBackend):
    """Normal username / password login. If the plan allows N users, only the first N may log in."""

    def user_can_authenticate(self, user):
        if not super().user_can_authenticate(user):
            return False
        if user.is_superuser:
            return True
        limit = licence.status(appsettings.current()).max_users
        if not limit:
            return True
        allowed = plan_users(type(user)).values_list("pk", flat=True)[:limit]
        return user.pk in set(allowed)


class PlanAuthenticationForm(AuthenticationForm):
    """Login form that explains when the plan's user limit, not the password, stopped the login."""

    def clean(self):
        try:
            return super().clean()
        except forms.ValidationError:
            user = get_user_model().objects.filter(username=self.cleaned_data.get("username")).first()
            if user and user.is_active and user.check_password(self.cleaned_data.get("password") or ""):
                raise forms.ValidationError(
                    ui(
                        "Your subscription plan allows {n} users. Ask your administrator.",
                        n=licence.status(appsettings.current()).max_users,
                    ),
                    code="plan_limit",
                ) from None
            raise
