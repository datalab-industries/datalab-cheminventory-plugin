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
    inventory_number: int | None = None

    def __init__(self, inventory_number: int | None = None):
        self.api_url = CHEMINVENTORY_API_URL
        self.auth_token = os.getenv("CHEMINVENTORY_API_KEY")
        if self.auth_token is None:
            raise ValueError("CHEMINVENTORY_API_KEY environment variable not set.")
        self.inventory_number = inventory_number

    def initialize(self, target_inventory: int | None = None) -> tuple[int, str]:
        """Initialises the API connection.

        If `target_inventory` (or one set on the instance) is provided, validates
        that the API key has access to it (via `user.inventory` and
        `user.otherInventories` in `/general/getdetails`) and switches the
        server-side active inventory via `/navbar/switchinventory` so that
        subsequent endpoints (which mostly do not accept an explicit `inventory`
        body parameter) operate on that inventory. Otherwise, the user's
        currently-active inventory is used as-is.

        Returns the resolved inventory number and name.
        """
        # Fetch unscoped so the response enumerates all accessible inventories.
        previous, self.inventory_number = self.inventory_number, None
        try:
            details = self.post("/general/getdetails")
        finally:
            self.inventory_number = previous
        user = details["user"]
        default_id = int(user["inventory"])
        default_name = user["inventoryname"]
        others = {
            int(o["inventory"]): o.get("name", "?")
            for o in user.get("otherInventories", [])
            if o.get("inventory") is not None
        }

        target = target_inventory if target_inventory is not None else self.inventory_number
        if target is None:
            self.inventory_number = default_id
            return default_id, default_name

        target = int(target)
        accessible = {default_id: default_name, **others}
        if target not in accessible:
            raise ValueError(
                f"Inventory {target} is not accessible by this API key. "
                f"Accessible inventories: {accessible}"
            )

        if target != default_id:
            # Server-side switch is required: most endpoints (e.g. /inventorymanagement/export)
            # ignore an `inventory` body parameter and always operate on the user's currently
            # active inventory. /navbar/switchinventory changes that active inventory.
            self.post("/navbar/switchinventory", body={"newinventory": target})

        self.inventory_number = target
        return target, accessible[target]

    def list_inventories(self) -> tuple[tuple[int, str], list[tuple[int, str]]]:
        """Returns ((active_id, active_name), [(other_id, other_name), ...])."""
        previous, self.inventory_number = self.inventory_number, None
        try:
            details = self.post("/general/getdetails")
        finally:
            self.inventory_number = previous
        user = details["user"]
        active = (int(user["inventory"]), user["inventoryname"])
        others = [
            (int(o["inventory"]), o.get("name", "?"))
            for o in user.get("otherInventories", [])
            if o.get("inventory") is not None
        ]
        return active, others

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
        body = {"authtoken": self.auth_token}
        if self.inventory_number is not None:
            body["inventory"] = self.inventory_number
        return body

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
