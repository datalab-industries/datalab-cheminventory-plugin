from importlib.metadata import version
import httpx

__version__ = version("datalab-cheminventory-plugin")

class ChemInventoryClient:

    api_url: str = "https://app.cheminventory.net/api"
    api_key: str | None = None
    _session: httpx.Client | None = None
    timeout: httpx.Timeout = httpx.Timeout(60.0, read=5.0)

    def __init__(self, inventory: int = 0, api_key: str | None = None):

        self.api_key = api_key
        resp = self.session.post(f"{self.api_url}/general/getdetails", json={"authtoken": self.api_key})
        if resp.status_code != 200:
            raise ValueError(f"Bad response from cheminventory: {resp.content}")
        try:
            json_resp = resp.json()
        except Exception:
            raise ValueError(f"Bad response from cheminventory: {resp.content}")

        if json_resp["status"] != "success":
            raise ValueError(f"Bad response from cheminventory: {resp.content}")

        self.inventory_number = json_resp["data"]["user"]["inventory"]
        self.inventory_name = json_resp["data"]["user"]["inventoryname"]
        print(f"Connected to ChemInventory: {self.inventory_name} ({self.inventory_number})")


    def get_inventory(self) -> dict:

        resp = self.session.post(f"{self.api_url}/inventorymanagement/export", json={"authtoken": self.api_key})

        if resp.status_code != 200:
            raise ValueError(f"Bad response from cheminventory: {resp.content}")

        try:
            json_resp = resp.json()
        except Exception:
            raise ValueError(f"Bad response from cheminventory: {resp.content}")

        if json_resp["status"] != "success":
            raise ValueError(f"Bad response from cheminventory: {resp.content}")

        inventory = json_resp["data"]["rows"]
        return inventory

    @property
    def session(self) -> httpx.Client:
        if self._session is None:
            return httpx.Client(timeout=self.timeout)
        return self._session
