import datetime
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

DEFAULT_LOCATION_NAME = "datalab"
"""A custom location to use for 'virtual' samples that have
been synced from datalab to cheminventory.
"""

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

    def get_custom_fields(self) -> dict[str, str]:
        """Returns a mapping from custom field names to cheminventory custom field
        IDs (with the appropriate sf- or cf- prefix for substance or container fields,
        respectively).
        """
        custom_fields: dict[str, str] = {}

        resp = self.session.post(
            f"{self.api_url}/customfields/get",
            json={"authtoken": self.api_key, "inventory": self.inventory_number},
        )

        if resp.status_code != 200:
            raise ValueError(f"Bad response from cheminventory: {resp.content}")

        try:
            json_resp = resp.json()
        except Exception:
            raise ValueError(f"Bad response from cheminventory: {resp.content}")

        if json_resp["status"] != "success":
            raise ValueError(f"Bad response from cheminventory: {resp.content}")

        for field in json_resp["data"].get("container", []):
            custom_fields[field["name"]] = f"cf-{field['id']}"
        for field in json_resp["data"].get("substance", []):
            custom_fields[field["name"]] = f"sf-{field['id']}"

        return custom_fields

    @property
    def session(self) -> httpx.Client:
        if self._session is None:
            return httpx.Client(timeout=self.timeout)
        return self._session

    def add_container_to_cheminventory(self, container: dict[str, Any]) -> None:
        """Add a container to the cheminventory."""

        resp = self.session.post(
            f"{self.api_url}/container/add",
            json={"authtoken": self.api_key, "data": [container]},
        )

        if resp.status_code != 200:
            raise ValueError(f"Bad response from cheminventory: {resp.content}")

        try:
            json_resp = resp.json()
        except Exception:
            raise ValueError(f"Bad response from cheminventory: {resp.content}")

        if json_resp["status"] != "success":
            raise ValueError(f"Bad response from cheminventory: {resp.content}")

    def map_datalab_entry_to_cheminventory_container(
        self,
        entry: dict[str, Any],
        location_id: int,
        substance_id: int | None,
        custom_fields: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Map a datalab `starting_material` entry to a new cheminventory container."""
        container: dict[str, str | int | None] = {}
        container["name"] = entry.get("name")
        if container["name"] is None:
            container["name"] = "Unknown"

        if custom_fields and "DataLab ID" in custom_fields:
            # Get ID mapping of custom field for datalab ID
            container[f"{custom_fields['DataLab ID']}"] = entry["refcode"]

        if entry.get("date"):
            container["dateacquired"] = datetime.datetime.fromisoformat(entry["date"]).strftime(
                "%Y-%m-%d"
            )

        container["locationid"] = location_id
        container["substanceid"] = substance_id
        return container

    def map_inventory_row(
        self, row: dict[str, Any], custom_fields: dict[str, str] | None = None
    ) -> dict[str, Any]:
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
        starting_material["description"] = row["comments"] if row["comments"] != "None" else ""
        starting_material["status"] = "disposed" if row["disposed"] == "1" else "available"

        if custom_fields:
            if "DataLab ID" in custom_fields:
                value = row.get(custom_fields["DataLab ID"])
                if value:
                    starting_material["refcode"] = value
            if "Identifying #" in custom_fields:
                value = row.get(custom_fields["Identifying #"])
                if value:
                    starting_material["description"] += f"\nIdentifying #: {value}"  # type: ignore
            if "Form type" in custom_fields:
                value = row.get(custom_fields["Form type"])
                if value:
                    starting_material["description"] += f"\nForm type: {value}"  # type: ignore

        return starting_material

    def get_substance_id(self, name: str, cas: str | None) -> int:
        if not cas:
            cas = "N/A"
        resp = self.session.post(
            f"{self.api_url}/container/getsubstance",
            json={"authtoken": self.api_key, "cas": cas, "name": name},
        )

        if resp.status_code != 200:
            raise ValueError(f"Bad response from cheminventory: {resp.content}")

        try:
            json_resp = resp.json()
        except Exception:
            raise ValueError(f"Bad response from cheminventory: {resp.content}")

        if json_resp["status"] != "success":
            raise ValueError(f"Bad response from cheminventory: {resp.content}")

        if len(json_resp["data"]) == 0:
            raise ValueError("No substance found with CAS N/A and name Unknown.")

        # Get the first substance IDs
        return json_resp["data"][0]["id"]

    def get_location_id(self, name: str | None) -> int:
        if name is None:
            name = DEFAULT_LOCATION_NAME

        resp = self.session.post(f"{self.api_url}/location/load", json={"authtoken": self.api_key})

        if resp.status_code != 200:
            raise ValueError(f"Bad response from cheminventory: {resp.content}")

        try:
            json_resp = resp.json()
        except Exception:
            raise ValueError(f"Bad response from cheminventory: {resp.content}")

        if json_resp["status"] != "success":
            raise ValueError(f"Bad response from cheminventory: {resp.content}")

        # Find virtual location called "datalab" in list
        for location in json_resp["data"]:
            if location["name"] == name:
                return location["id"]

        for location in json_resp["data"]:
            if location["name"] == DEFAULT_LOCATION_NAME:
                return location["id"]

        raise ValueError(r"No location called {name!r} found in cheminventory.")

    def sync_to_cheminventory(
        self,
        existing_ids_or_refcodes: set[str],
        deleted_ids_or_refcodes: set[str],
        dry_run: bool = True,
    ) -> None:
        """Fetch inventory and upload to cheminventory, syncing
        only those items that have IDs that do not match the existing IDs.

        Parameters:
            existing_ids_or_refcodes: A set of item IDs that were already found in cheminventory.

        """
        # get datalab entries
        custom_fields = self.get_custom_fields()

        found: int = 0

        with DatalabClient(self.datalab_api_url) as datalab_client:
            datalab_inventory = datalab_client.get_items(item_type="starting_materials")
            for entry in rich.progress.track(
                datalab_inventory, description="Exporting datalab inventory to cheminventory"
            ):
                if (
                    str(entry["item_id"]) not in existing_ids_or_refcodes
                    and entry["refcode"] not in existing_ids_or_refcodes
                    and str(entry["item_id"]) not in deleted_ids_or_refcodes
                    and str(entry["refcode"]) not in deleted_ids_or_refcodes
                ):
                    found += 1
                    if dry_run:
                        pprint(entry)
                    # map datalab entry to cheminventory container
                    substance_id = None
                    if not dry_run:
                        substance_id = self.get_substance_id(entry["name"], entry.get("CAS"))
                    location_id = self.get_location_id(entry.get("location"))
                    container = self.map_datalab_entry_to_cheminventory_container(
                        entry,
                        custom_fields=custom_fields,
                        location_id=location_id,
                        substance_id=substance_id,
                    )
                    if not dry_run:
                        # add container to cheminventory
                        self.add_container_to_cheminventory(container)
                        pprint(f"Added {container['name']} to cheminventory.")
                    else:
                        pprint(f"Would add {container['name']}/{entry['item_id']} to cheminventory")

        pprint(f"[green]Found {found} items to add to cheminventory.[/green]")

    def get_deleted_inventory_ids(self) -> set[str]:
        resp = self.session.post(
            f"{self.api_url}/inventorymanagement/deletedcontainers/get",
            json={"authtoken": self.api_key},
        )
        if resp.status_code != 200:
            raise ValueError(f"Bad response from cheminventory: {resp.content}")

        try:
            json_resp = resp.json()
        except Exception:
            raise ValueError(f"Bad response from cheminventory: {resp.content}")

        if json_resp["status"] != "success":
            raise ValueError(f"Bad response from cheminventory: {resp.content}")

        return {str(d.get("id")) for d in json_resp["data"] if d.get("id") is not None}

    def sync_to_datalab(
        self, collection_id: str | None = None, dry_run: bool = True
    ) -> tuple[set[str], set[str]]:
        """Fetch inventory and upload to Datalab.

        Returns:
            A set of item IDs that were found in cheminventory.

        """
        if dry_run:
            pprint("Dry run mode: no datalab items will be created.")

        ids_found = set()

        with DatalabClient(self.datalab_api_url) as datalab_client:
            successes = 0
            failures = 0
            updated = 0
            total = 0

            custom_fields = self.get_custom_fields()

            ids_deleted = self.get_deleted_inventory_ids()

            for row in rich.progress.track(
                self.get_inventory(), description="Importing cheminventory"
            ):
                entry = self.map_inventory_row(row, custom_fields=custom_fields)
                files = []
                if not dry_run:
                    files = self.get_linked_files(row["substanceid"])

                ids_found.add(str(entry["item_id"]))
                if entry.get("refcode"):
                    ids_found.add(str(entry["refcode"]))

                existing_fnames = set()
                total += 1
                if dry_run:
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

            if not dry_run:
                pprint(f"\n[green]Created {successes} items.[/green]")
                if updated > 0:
                    pprint(f"[yellow]Updated {updated} items.[/yellow]")
                if failures > 0:
                    pprint(f"[red]Failed to create {failures} items.[/red]")

            if dry_run:
                pprint(f"\n[green]Found {total} items.[/green]")

        return ids_found, ids_deleted


def _main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Sync from cheminventory to datalab.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not create items in datalab.",
    )
    args = parser.parse_args()
    client = ChemInventoryClient()
    cheminventory_ids, cheminventory_deleted_ids = client.sync_to_datalab(dry_run=args.dry_run)
    client.sync_to_cheminventory(
        dry_run=args.dry_run,
        existing_ids_or_refcodes=cheminventory_ids,
        deleted_ids_or_refcodes=cheminventory_deleted_ids,
    )


if __name__ == "__main__":
    _main()
