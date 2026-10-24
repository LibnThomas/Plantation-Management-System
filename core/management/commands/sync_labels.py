from django.core.management.base import BaseCommand

from core.i18n import sync_labels


class Command(BaseCommand):
    help = "Add rows for new texts to Labels & Translations (never overwrites edits)."

    def handle(self, *args, **opts):
        new, changed = sync_labels()
        self.stdout.write(self.style.SUCCESS(f"Labels: {new} added, {changed} updated."))
