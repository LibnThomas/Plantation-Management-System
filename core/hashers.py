from django.contrib.auth.hashers import PBKDF2PasswordHasher


class MobilePBKDF2PasswordHasher(PBKDF2PasswordHasher):
    """Phone app only (settings.MOBILE_APP): logging in must not take seconds on a phone. Passwords made on a
    server keep working (the normal hasher is listed too) and are re-hashed at the next login."""

    algorithm = "pbkdf2_sha256_m"
    iterations = 120_000
