from importlib.metadata import version
import httpx
import os
from datalab_api import DatalabClient, DuplicateItemError

__version__ = version("datalab-cheminventory-plugin")

DATALAB_URL = os.getenv("DATALAB_API_URL")

class ChemInventoryClient:

    api_url: str = "https://app.cheminventory.net/api"
    api_key: str | None = None
    _session: httpx.Client | None = None
    timeout: httpx.Timeout = httpx.Timeout(60.0, read=5.0)

    def __init__(self, inventory: int = 0, api_key: str | None = None):

        self.api_key = api_key
        if api_key is None:
            self.api_key = os.getenv("CHEMINVENTORY_API_KEY")

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

    def map_inventory_row(self, row) -> dict:
        starting_material = {}
        starting_material["item_id"] = row["id"]
        starting_material["Barcode"] = row["barcode"] or None
        starting_material["Container Name"] = row["name"]
        starting_material["Container Size"] = row["size"]
        starting_material["Supplier"] = row["supplier"]
        starting_material["Substance CAS"] = row["cas"]
        starting_material["GHS H-codes"] = row["hcodes"]
        starting_material["SMILES"] = row["smiles"]
        starting_material["Unit"] = row["unit"]
        starting_material["Molecular Formula"] = row["molecularformula"] or None
        starting_material["Molecular Weight"] = row["molecularweight"] or None
        starting_material["Location"] = row["location"]
        starting_material["Date Acquired"] = row["dateacquired"] or None
        starting_material["type"] = "starting_materials"

        return starting_material


    def sync_to_datalab(self, datalab_url: str, collection_id: str = "cheminventory") -> None:
        """Fetch inventory and upload to Datalab."""
        inventory = self.get_inventory()
        entries = [self.map_inventory_row(row) for row in inventory]

        datalab_client = DatalabClient(datalab_url)
        for entry in entries:
            try:
                datalab_client.create_item(
                    entry["item_id"],
                    entry["type"],
                    entry,
                    collection_id=collection_id,
                )
                print("success ", entry.get("Barcode"))
            except (KeyError, DuplicateItemError):
                print(f"dupe {entry.get('Barcode')}")
            except Exception as e:
                print(f"Error creating item {entry.get('Barcode')}: {e}")

if __name__ == "__main__":
    client = ChemInventoryClient()
    client.sync_to_datalab(DATALAB_URL)
