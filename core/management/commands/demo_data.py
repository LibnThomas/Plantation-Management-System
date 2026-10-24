from django.core.management.base import BaseCommand

from core import demo


class Command(BaseCommand):
    help = "Create (or with --reset rebuild) the sample data used by Demo mode. Real data is never touched."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Empty the demo database first")
        parser.add_argument("--months", type=int, default=18)

    def handle(self, *args, **opts):
        if demo.has_data() and not opts["reset"]:
            self.stdout.write("Demo data already exists (use --reset to rebuild it).")
            return
        info = demo.generate(months=opts["months"], reset=opts["reset"])
        self.stdout.write(
            self.style.SUCCESS(f"Demo data ready: {info['employees']} employees, {info['from']} to {info['to']}.")
        )
