import os
import tempfile
from importlib.metadata import version
from pathlib import Path
from re import A
from typing import Any

import httpx
import rich.progress
import rich.traceback
from datalab_api import DatalabClient, DuplicateItemError
from rich import print as pprint

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

        resp = self.session.post(
            f"{self.api_url}/general/getdetails", json={"authtoken": self.api_key}
        )
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
        pprint(f"Connected to ChemInventory: {self.inventory_name} ({self.inventory_number})")

    def get_inventory(self) -> dict:
        resp = self.session.post(
            f"{self.api_url}/inventorymanagement/export", json={"authtoken": self.api_key}
        )

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

    def get_linked_files(
        self, substanceid: int, mimetypes: tuple[str] = ("application/pdf",)
    ) -> list[Path]:
        if substanceid is None:
            raise ValueError("No substanceid provided.")

        file_paths = []
        resp = self.session.post(
            f"{self.api_url}/filestore/getlinkedfiles",
            json={"authtoken": self.api_key, "substanceid": substanceid},
        )
        file_ids: list[int] = []
        if resp.status_code == 200 and (json_resp := resp.json())["status"] == "success":
            if len(json_resp.get("data", [])) > 0:
                file_ids = [
                    entry["id"] for entry in json_resp["data"] if entry["mimetype"] in mimetypes
                ]

        tmpdir_path = Path(tempfile.mkdtemp())

        for f in file_ids:
            file_response = self.session.post(
                f"{self.api_url}/filestore/download", json={"authtoken": self.api_key, "fileid": f}
            )
            if (
                file_response.status_code == 200
                and (json_resp := file_response.json())["status"] == "success"
            ):
                file_url = str(json_resp["data"])
            else:
                raise ValueError(f"Bad response from cheminventory: {file_response.content}")

            file_path = tmpdir_path / f"{f}.pdf"

            with httpx.stream("GET", file_url) as response:
                with open(file_path, "wb") as file:
                    for chunk in response.iter_bytes():
                        file.write(chunk)
                file_paths.append(file_path)

            pprint(f"Downloaded file {f}.pdf to {tmpdir_path}")

        return file_paths

    @property
    def session(self) -> httpx.Client:
        if self._session is None:
            return httpx.Client(timeout=self.timeout)
        return self._session

    def map_inventory_row(self, row: dict[str, Any]) -> tuple[dict[str, Any], list]:
        starting_material: dict[str, str | int | None] = {}
        starting_material["item_id"] = str(row["id"])
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
        starting_material["description"] = row["comments"] if row["comments"] != "None" else None
        starting_material["status"] = "disposed" if row["disposed"] == "1" else "available"
        file_paths = self.get_linked_files(row["substanceid"])

        return starting_material, file_paths

    def sync_to_datalab(self, collection_id: str | None = None, dryrun: bool = True) -> None:
        """Fetch inventory and upload to Datalab."""

        if dryrun:
            pprint("Dry run mode: no datalab items will be created.")

        with DatalabClient(self.datalab_api_url) as datalab_client:
            successes = 0
            failures = 0
            updated = 0
            total = 0

            for row in rich.progress.track(
                self.get_inventory(), description="Importing cheminventory"
            ):
                entry, files = self.map_inventory_row(row)
                existing_fnames = set()
                total += 1
                if dryrun:
                    pprint(entry)
                else:
                    try:
                        try:
                            datalab_client.create_item(
                                entry["item_id"],
                                entry["type"],
                                entry,
                                collection_id=collection_id,
                            )

                            successes += 1
                            pprint(
                                f"[green]✓\t{entry.get('item_id')}\t{entry.get('Barcode')}[/green]"
                            )
                        except DuplicateItemError:
                            # If the item already exists, pull it and see if it needs to be updated
                            existing_item = datalab_client.get_item(entry["item_id"])
                            if existing_item["type"] != entry["type"]:
                                raise ValueError(
                                    f"Item {entry['item_id']} already exists with type {existing_item['type']}, but we are trying to create it with type {entry['type']}."
                                )

                            response = datalab_client.update_item(
                                entry["item_id"],
                                entry,
                            )
                            if response["status"] != "success":
                                raise RuntimeError(f"Failed to update item: {response['message']}")

                            updated += 1
                            existing_fnames = {f["original_name"] for f in existing_item["files"]}
                            pprint(
                                f"[yellow]·\t{entry.get('item_id')}\t{entry.get('Barcode')}[/yellow]"
                            )

                        if files:
                            new_fnames = {f.name for f in files}
                            update_file_set = new_fnames - existing_fnames
                            for f in files:
                                if f.name in update_file_set:
                                    file_resp = datalab_client.upload_file(
                                        entry["item_id"],
                                        f,
                                    )
                                    file_id = file_resp["file_id"]
                                    datalab_client.create_data_block(
                                        item_id=entry["item_id"],
                                        block_type="media",
                                        file_ids=file_id,
                                    )
                                    pprint(
                                        f"[green]✓\tAdded file to {entry.get('item_id')}\t{entry.get('Barcode')}[/green]"
                                    )

                    except Exception as e:
                        failures += 1
                        pprint(
                            f"[red]✗\t{entry.get('item_id')}\t{entry.get('Barcode')}:\n{e}[/red]"
                        )

            if not dryrun:
                pprint(f"\n[green]Created {successes} items.[/green]")
                if updated > 0:
                    pprint(f"[yellow]Updated {updated} items.[/yellow]")
                if failures > 0:
                    pprint(f"[red]Failed to create {failures} items.[/red]")

            if dryrun:
                pprint(f"\n[green]Found {total} items.[/green]")


if __name__ == "__main__":
    client = ChemInventoryClient()
    client.sync_to_datalab(dryrun=False)
