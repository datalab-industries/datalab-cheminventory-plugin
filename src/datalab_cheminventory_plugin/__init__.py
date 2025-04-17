import os
from re import A
import tempfile
from pathlib import Path
from importlib.metadata import version
import httpx
import rich.traceback
import rich.progress
from rich import print

from datalab_api import DatalabClient, DuplicateItemError

rich.traceback.install(show_locals=True)

__version__ = version("datalab-cheminventory-plugin")

class ChemInventoryClient:

    api_url: str = "https://app.cheminventory.net/api"
    api_key: str | None = None
    _session: httpx.Client | None = None
    timeout: httpx.Timeout = httpx.Timeout(60.0, read=5.0)
    datalab_api_url: str

    def __init__(self, inventory: int = 0, api_key: str | None = None):

        self.api_key = api_key
        if api_key is None:
            self.api_key = os.getenv("CHEMINVENTORY_API_KEY")

        datalab_api_url = os.getenv("DATALAB_API_URL")
        if datalab_api_url is None:
            raise ValueError("DATALAB_API_URL environment variable not set.")
        self.datalab_api_url = datalab_api_url

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

    def get_linked_files(self, substanceid: int, mimetypes: list[str] = ["application/pdf"]) -> list[Path]:

        if substanceid is None:
            raise ValueError("No substanceid provided.")

        file_paths = []
        resp = self.session.post(f"{self.api_url}/filestore/getlinkedfiles", json={"authtoken": self.api_key, "substanceid": substanceid})
        file_ids: list[int] = []
        if resp.status_code == 200 and (json_resp := resp.json())["status"] == "success":
            if len(json_resp.get("data", [])) > 0:
                file_ids = [entry["id"] for entry in json_resp["data"] if entry["mimetype"] in mimetypes]

        tmpdir_path = Path(tempfile.mkdtemp())

        for f in file_ids:
            file_response = self.session.post(f"{self.api_url}/filestore/download", json={"authtoken": self.api_key, "fileid": f})
            if file_response.status_code == 200 and (json_resp := file_response.json())["status"] == "success":
                file_url = str(json_resp["data"])
            else:
                raise ValueError(f"Bad response from cheminventory: {file_response.content}")

            file_path = tmpdir_path / f"{f}.pdf"

            with httpx.stream("GET", file_url) as response:
                with open(file_path, "wb") as file:
                    for chunk in response.iter_bytes():
                        file.write(chunk)
                file_paths.append(file_path)

            print(f"Downloaded file {f}.pdf to {tmpdir_path}")

        return file_paths

    @property
    def session(self) -> httpx.Client:
        if self._session is None:
            return httpx.Client(timeout=self.timeout)
        return self._session

    def map_inventory_row(self, row) -> dict:
        files = []
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
        file_paths = self.get_linked_files(row["substanceid"])

        return starting_material, files


    def sync_to_datalab(self, collection_id: str | None = None, dryrun: bool = True) -> None:
        """Fetch inventory and upload to Datalab."""

        if dryrun:
            print("Dry run mode: no datalab items will be created.")

        with DatalabClient(self.datalab_api_url) as datalab_client:
            successes = 0 
            failures = 0
            duplicates = 0
            total = 0

            for row in rich.progress.track(self.get_inventory(), description="Importing cheminventory"):
                entry, files = self.map_inventory_row(row)
                total += 1
                if dryrun:
                    print(entry)
                else:
                    try:
                        datalab_client.create_item(
                            entry["item_id"],
                            entry["type"],
                            entry,
                            collection_id=collection_id,
                        )

                        for f in files:
                            datalab_client.upload_file(
                                entry["item_id"],
                                f,
                            )
                        successes += 1
                        print(f"[green]✓\t{entry.get('item_id')}\t{entry.get('Barcode')}[/green]")
                    except (KeyError, DuplicateItemError):
                        duplicates += 1
                        print(f"[yellow]·\t{entry.get('item_id')}\t{entry.get('Barcode')}[/yellow]")
                    except Exception as e:
                        failures += 1
                        print(f"[red]✗\t{entry.get('item_id')}\t{entry.get('Barcode')}:\n{e}[/red]")

            if not dryrun:
                print(f"\n[green]Created {successes} items.[/green]")
                if duplicates > 0:
                    print(f"[yellow]Skipped {duplicates} items (already exist).[/yellow]")
                if failures > 0:
                    print(f"[red]Failed to create {failures} items.[/red]")

            if dryrun:
                print(f"\n[green]Found {total} items.[/green]")

if __name__ == "__main__":
    client = ChemInventoryClient()
    client.sync_to_datalab()
