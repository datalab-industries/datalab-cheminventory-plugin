import os
from importlib.metadata import version
from typing import Any

import httpx

CHEMINVENTORY_API_URL = os.getenv("CHEMINVENTORY_API_URL", "https://app.cheminventory.net/api")


class ChemInventoryAPI:
    """A wrapper for the cheminventory API that performs
    error handling, authentication and parsing of the JSON response.
    """

    timeout: httpx.Timeout = httpx.Timeout(60.0, read=5.0)
    user_agent = f"datalab-cheminventory-plugin/{version('datalab-cheminventory-plugin')}"
    _session: httpx.Client | None = None

    def __init__(self):
        self.api_url = CHEMINVENTORY_API_URL
        self.auth_token = os.getenv("CHEMINVENTORY_API_KEY")
        if self.auth_token is None:
            raise ValueError("CHEMINVENTORY_API_KEY environment variable not set.")

    def initialize(self) -> tuple[int, str]:
        """Initialises API connection and returns the connected
        inventory number and name.
        """
        details = self.post("/general/getdetails")
        inventory_number = details["user"]["inventory"]
        inventory_name = details["user"]["inventoryname"]
        return inventory_number, inventory_name

    @property
    def session(self) -> httpx.Client:
        if self._session is None:
            return httpx.Client(timeout=self.timeout)
        return self._session

    def __del__(self):
        if self._session is not None:
            self._session.close()

    @property
    def auth_body(self):
        return {
            "authtoken": self.auth_token,
        }

    @property
    def headers(self):
        return {
            "Content-Type": "application/json",
            "User-Agent": self.user_agent,
        }

    def _post(self, endpoint: str, body: dict[str, Any] | None = None) -> httpx.Response:
        """Make a POST request to the cheminventory API, returning the raw response."""
        if body is None:
            body = {}
        url = f"{self.api_url.rstrip('/')}/{endpoint.lstrip('/')}"
        response = httpx.post(url, json=self.auth_body | body, headers=self.headers)
        return response

    def post(
        self, endpoint: str, body: dict[str, Any] | None = None, results_key: str = "data"
    ) -> dict:
        """Make a POST request to the cheminventory API, returning the
        `results_key` (default: `data`) of the parsed JSON response or erroring
        if unsuccessful.

        Parameters:
            endpoint: The cheminventory API endpoint to call.
            body: The body of the request.

        Raises:
            RuntimeError: If the response is not 200 or if the response
                does not contain a `data` key.

        """
        response = self._post(endpoint, body)
        if response.status_code != 200:
            raise RuntimeError(
                f"Bad response from cheminventory ({response.status_code=}): {response.content}"
            )

        try:
            json_resp = response.json()
        except Exception:
            raise RuntimeError(f"Bad response from cheminventory: {response.content}")

        if json_resp["status"] != "success":
            raise RuntimeError(f"Error reported by cheminventory: {json_resp}")

        if results_key not in json_resp:
            raise RuntimeError(f"Response does not contain {results_key} key: {json_resp}")

        return json_resp[results_key]
