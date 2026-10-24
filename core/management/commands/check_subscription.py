"""Fetch the valid till date from the subscription API now (e.g. from cron)."""

from django.core.management.base import BaseCommand, CommandError

from core import subscription_api
from core.models import AppSettings


class Command(BaseCommand):
    help = "Check the subscription API now and update the valid till date."

    def handle(self, *args, **options):
        app = AppSettings.load()
        if not (subscription_api.api_on(app) and subscription_api.api_url(app)):
            raise CommandError("The subscription API is switched off or has no URL (App Settings → Subscription).")
        ok, message = subscription_api.check(app)
        if not ok:
            raise CommandError(message)
        self.stdout.write(message)
