"""
Vendor tool: make a licence key for a customer installation.

    python manage.py issue_licence --key ~/secure/pms_vendor_private_key.pem \\
        --customer "Green Valley Estate" --install ABCD-1234-EF56 --months 12 --users 10

Needs the vendor's private key (never ship it with the app). Prints the key to send to the customer.
"""

import calendar
import datetime as dt
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core import licence


def add_months(d, months):
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


class Command(BaseCommand):
    help = "Issue a signed licence key (vendor only: needs the private key)."

    def add_arguments(self, parser):
        parser.add_argument("--key", required=True, help="Path of the vendor private key (PEM)")
        parser.add_argument("--customer", required=True)
        parser.add_argument("--install", default="*", help="Installation ID shown in App Settings (* = any)")
        parser.add_argument("--until", help="Valid until YYYY-MM-DD")
        parser.add_argument("--months", type=int, help="...or valid for this many months from today")
        parser.add_argument("--days", type=int, help="...or for this many days from today (e.g. 30)")
        parser.add_argument("--plan", default="Standard")
        parser.add_argument("--users", type=int, default=0, help="Max users (0 = no limit)")
        parser.add_argument("--grace", type=int, default=None, help="Grace days after the end date")
        parser.add_argument("--support", default="", help="Contact shown to the customer for renewal")

    def handle(self, *args, **o):
        today = dt.date.today()
        if o["until"]:
            until = dt.date.fromisoformat(o["until"])
        elif o["months"]:
            until = add_months(today, o["months"]) - dt.timedelta(days=1)
        elif o["days"]:
            until = today + dt.timedelta(days=o["days"] - 1)
        else:
            raise CommandError("Give --until, --months or --days.")
        pem = Path(o["key"]).expanduser().read_bytes()
        key = licence.issue(
            pem,
            o["customer"],
            until,
            install_id=o["install"],
            plan=o["plan"],
            max_users=o["users"],
            grace_days=o["grace"],
            support=o["support"],
        )
        licence.decode(key, None if o["install"] == "*" else o["install"])  # self-check with the public key
        self.stdout.write(f"Valid until {until:%d/%m/%y} for {o['customer']} ({o['install']})\n\n{key}\n")
