"""Vendor tool: create a new signing key pair (only needed to replace the vendor key)."""

import base64
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create a vendor key pair. Keep the private key secret; put the public key in core/licence.py."

    def add_arguments(self, parser):
        parser.add_argument("out", help="File to write the private key to (PEM)")

    def handle(self, *args, **o):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        path = Path(o["out"]).expanduser()
        if path.exists():
            raise CommandError(f"{path} already exists.")
        key = Ed25519PrivateKey.generate()
        path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
        path.chmod(0o600)
        pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.stdout.write(
            f"Private key: {path}\nAdd this public key to VENDOR_PUBLIC_KEYS in core/licence.py:\n"
            f"{base64.b64encode(pub).decode()}\n"
        )
