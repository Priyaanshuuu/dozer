"""Client for the sample's Dozer-backed profile API."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from profile_api import FIELDS, customer_id


class ProfileError(RuntimeError):
    pass


class ProfileClient:
    def __init__(self, base_url=None):
        self.base_url = (base_url or os.environ.get("PROFILE_API_URL", "http://localhost:58000")).rstrip("/")

    def fetch(self, identifier):
        identifier = customer_id(identifier)
        try:
            with urlopen(f"{self.base_url}/customers/{identifier}", timeout=10) as response:
                profile = json.load(response)
        except HTTPError as error:
            error.close()
            if error.code == 404:
                raise ProfileError(f"Customer {identifier} was not found.") from error
            raise ProfileError("Customer profile API is unavailable.") from error
        except (URLError, OSError, ValueError) as error:
            raise ProfileError("Could not read the customer profile API.") from error
        if not isinstance(profile, dict) or not all(field in profile for field in FIELDS):
            raise ProfileError("Incomplete customer profile.")
        try:
            for field in FIELDS:
                if field not in ("display_name", "current_card"):
                    value = profile[field]
                    if isinstance(value, bool) or not isinstance(value, (int, str)):
                        raise ValueError("invalid numeric field")
                    profile[field] = int(value)
                    if profile[field] < 0:
                        raise ValueError("negative profile field")
            if profile["customer_id"] != identifier:
                raise ValueError("customer mismatch")
        except (TypeError, ValueError) as error:
            raise ProfileError("Invalid or mismatched customer profile.") from error
        return {field: profile[field] for field in FIELDS}
