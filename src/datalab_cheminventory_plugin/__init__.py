import datetime
import os
import tempfile
from importlib.metadata import version
from pathlib import Path
from re import A
from typing import Any, Literal

import httpx
import rich.progress
import rich.traceback
from datalab_api import DatalabClient, DuplicateItemError
from rich import print as pprint

from ._api import ChemInventoryAPI

rich.traceback.install(show_locals=False)

__version__ = version("datalab-cheminventory-plugin")

CUSTOM_ID_FIELD = "DataLab ID"
"""The custom field name in cheminventory that will be used to store the
immutable datalab refcode, where necessary.
"""

DEFAULT_LOCATION_NAME = "datalab"
"""A custom location to use for 'virtual' samples that have
been synced from datalab to cheminventory.
"""


class ChemInventoryDatalabSyncer:
    """A class to sync cheminventory with functionality for syncing datalab
    starting materials with cheminventory containers.

    The entrypoint `.sync()` will first loop through the cheminventory and add
    any missing items to datalab, where 'missing' is determined by:

        - if a datalab `item_id` that matches the cheminventory `container_id`,
        - if the cheminventory defines a `CUSTOM_ID_FIELD` (default:
          `DataLab ID`)ustom field, then this will be compared with the datalab
          `refcode`s. If it does not, this custom field will be defined for
          future use.

    Any SDS files will be downloaded from cheminventory and uploaded to datalab
    as media blocks attached to the given entry.

    The second part of the sync will loop through the datalab starting materials
    and add any missing items to cheminventory, where 'missing' is determined by
    the inverse of the above, plus:

        - if the datalab item_id or refcode matches any ID in the cheminventory
          'deleted containers' list. In this case, the datalab entry will be
          preserved by default.
        - if the datalab item_id matches a barcode in cheminventory, this will
          not be synced (as it indicates that there may already be duplicates
          between datalab and cheminventory).

    datalab entries will be synced to cheminventory containers, with defined
    substances if a CAS or name matches an existing substance, or a new
    "unknown" substance if not.
    Similarly, the cheminventory 'shelf' will be extracted from the datalab
    `location`, but in the case that there is no match, a placeholder location
    'datalab' will be used.

    The sync is intended to be idempotent; running multiple times without any
    changes in the source databases should lead to no changes.
    This can be tested with `dry_run=True`.

    """

    _cheminventory: ChemInventoryAPI | None = None
    datalab_api_url: str
    inventory_name: str
    inventory_number: int

    dry_run: bool = False
    """Whether to actually create items in datalab or cheminventory."""

    c2d_only: bool = False
    """Whether to only sync in one direction, from cheminventory to datalab."""

    skip_files: bool = False
    """Whether to skip downloading files from cheminventory."""

    def __init__(
        self, dry_run: bool = False, skip_files: bool = False, c2d_only: bool = False
    ) -> None:
        datalab_api_url = os.getenv("DATALAB_API_URL")
        if datalab_api_url is None:
            raise ValueError("DATALAB_API_URL environment variable not set.")
        self.datalab_api_url = datalab_api_url

        self.dry_run = dry_run
        self.c2d_only = c2d_only
        self.skip_files = skip_files

        self.inventory_number, self.inventory_name = self.cheminventory.initialize()
        pprint(f"Connected to ChemInventory: {self.inventory_name} ({self.inventory_number})")

    def sync(self):
        """Perform the two-way sync from cheminventory to datalab and back."""
        cheminventory_ids, cheminventory_deleted_ids = self.sync_to_datalab(
            dry_run=self.dry_run, skip_files=self.skip_files
        )
        if not self.c2d_only:
            self.sync_to_cheminventory(
                dry_run=self.dry_run,
                existing_ids_or_refcodes=cheminventory_ids,
                deleted_ids_or_refcodes=cheminventory_deleted_ids,
            )

    @property
    def cheminventory(self) -> ChemInventoryAPI:
        """The cheminventory API wrapper, authenticated via
        the `CHEMINVENTORY_API_KEY` environment variable.

        """
        if self._cheminventory is None:
            self._cheminventory = ChemInventoryAPI()
        return self._cheminventory

    def get_inventory(self) -> dict:
        return self.cheminventory.post("/inventorymanagement/export")["rows"]

    def get_deleted_containers(self) -> dict:
        return self.cheminventory.post("/inventorymanagement/deletedcontainers/get")

    def get_linked_files(
        self, substanceid: int, mimetypes: tuple[str] = ("application/pdf",)
    ) -> list[Path]:
        """Find and download any linked files for a given substance ID.

        Files will be downloaded to a temporary directory and their paths are
        returned as a list.
        """
        if substanceid is None:
            raise ValueError("No substanceid provided.")

        file_paths = []
        file_ids = [
            entry["id"]
            for entry in self.cheminventory.post(
                "/filestore/getlinkedfiles", body={"substanceid": substanceid}
            )
            if entry["mimetype"] in mimetypes
        ]

        tmpdir_path = Path(tempfile.mkdtemp())

        for f in file_ids:
            file_url = str(self.cheminventory.post("/filestore/download", body={"fileid": f}))
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

        fields = self.cheminventory.post(
            "/customfields/get", body={"inventory": self.inventory_number}
        )

        for field in fields.get("container", []):
            custom_fields[field["name"]] = f"cf-{field['id']}"
        for field in fields.get("substance", []):
            custom_fields[field["name"]] = f"sf-{field['id']}"

        return custom_fields

    def add_container_to_cheminventory(self, container: dict[str, Any]) -> None:
        """Add a container to the cheminventory."""

        self.cheminventory.post(
            "/container/add",
            body={"data": [container]},
        )

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

        if custom_fields and CUSTOM_ID_FIELD in custom_fields:
            # Get ID mapping of custom field for datalab ID
            container[f"{custom_fields[CUSTOM_ID_FIELD]}"] = entry["refcode"]

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
        """Maps a cheminventory row to a datalab starting material entry."""
        starting_material: dict[str, str | int | None] = {}
        starting_material["item_id"] = str(row["id"])
        starting_material["barcode"] = row["barcode"] or None
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
            if CUSTOM_ID_FIELD in custom_fields:
                value = row.get(custom_fields[CUSTOM_ID_FIELD])
                if value:
                    starting_material["refcode"] = value
            if "Identifying #" in custom_fields:
                value = row.get(custom_fields["Identifying #"])
                if value:
                    starting_material["description"] += f"\nIdentifying #: {value}"  # type: ignore
            if "Lot Number" in custom_fields:
                value = row.get(custom_fields["Lot Number"])
                if value:
                    starting_material["description"] += f"\nLot number: {value}"  # type: ignore
            if "Form type" in custom_fields:
                value = row.get(custom_fields["Form type"])
                if value:
                    starting_material["description"] += f"\nForm type: {value}"  # type: ignore

        return starting_material

    def get_substance_id(self, name: str, cas: str | None) -> int:
        """Looks up the substance ID for a given name and (optional) CAS number,
        returning the first matching ID.

        """
        if not cas:
            cas = "N/A"

        substances = self.cheminventory.post(
            "/container/getsubstance", body={"cas": cas, "name": name}
        )
        if not substances:
            raise ValueError("No substance found with {cas=} and {name=}.")

        # Get the first substance IDs
        return substances[0]["id"]

    def get_location_id(self, name: str | None) -> int:
        """Looks for a location with the given name in cheminventory,
        if missing, returns the ID of the default specified location in
        `DEFAULT_LOCATION_NAME`.

        """
        if name is None:
            name = DEFAULT_LOCATION_NAME

        locations = self.cheminventory.post("/location/load")

        # Find virtual location called "datalab" in list
        for location in locations:
            if location["name"] == name:
                return location["id"]

        for location in locations:
            if location["name"] == DEFAULT_LOCATION_NAME:
                return location["id"]

        raise ValueError(
            r"No location matching {name!r} or {DEFAULT_LOCATION_NAME!r} found in cheminventory."
        )

    def set_custom_field(
        self,
        name: str,
        type_: Literal["text", "multilinetext", "number", "date", "url", "dropdown", "tags"],
        field_type: Literal["container", "substance"] = "container",
    ) -> None:
        """Creates a custom field in cheminventory with the given name and type."""
        self.cheminventory.post(
            "/customfields/save",
            body={
                "id": 0,
                "name": name,
                "type": type_,
                "fieldtype": field_type,
                "scope": "inventory",  # Not documented but was required to get this to work
            },
        )

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

        # get the custom field definitions from cheminventory
        custom_fields = self.get_custom_fields()
        if CUSTOM_ID_FIELD not in custom_fields:
            if not dry_run:
                self.set_custom_field(CUSTOM_ID_FIELD, "text", "container")
                custom_fields = self.get_custom_fields()

        found: int = 0

        # get datalab entries
        with DatalabClient(self.datalab_api_url) as datalab_client:
            datalab_inventory = datalab_client.get_items(item_type="starting_materials")
            for entry in rich.progress.track(
                datalab_inventory, description="Exporting datalab inventory to cheminventory"
            ):
                if (
                    str(entry["item_id"]) not in existing_ids_or_refcodes
                    and entry.get("refcode") not in existing_ids_or_refcodes
                    and str(entry["item_id"]) not in deleted_ids_or_refcodes
                    and entry.get("refcode") not in deleted_ids_or_refcodes
                ):
                    found += 1
                    if dry_run:
                        pprint(entry)
                    # map datalab entry to cheminventory container
                    substance_id = None
                    location_id: int = 1
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
        """Returns a list of deleted inventory IDs from cheminventory."""
        deleted_containers = self.cheminventory.post("/inventorymanagement/deletedcontainers/get")
        return {str(d.get("id")) for d in deleted_containers if d.get("id") is not None}

    def sync_to_datalab(
        self, collection_id: str | None = None, dry_run: bool = True, skip_files: bool = False
    ) -> tuple[set[str], set[str]]:
        """Fetch inventory and upload to datalab, updating items that already exist.

        Parameters:
            collection_id: Put the synced items into the given collection.
            dry_run: Whether to actually update datalab entries.
            skip_files: Whether to skip downloading files from cheminventory.

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

            inventory = self.get_inventory()
            for row in rich.progress.track(inventory, description="Importing cheminventory"):
                entry = self.map_inventory_row(row, custom_fields=custom_fields)
                files = []
                if not dry_run:
                    if not skip_files:
                        files = self.get_linked_files(row["substanceid"])

                # Accumulate cheminventory container IDs to avoid duplication
                ids_found.add(str(entry["item_id"]))

                # Refcodes currently get dropped when making a new starting material,
                # so we need to manually check if it exists already
                # This should be an edge case, as only items originating from datalab in the first instance
                # will have this refcode available.
                # If the refcode exists, then set the item ID from the refcode instead.
                if entry.get("refcode"):
                    ids_found.add(str(entry["refcode"]))
                    try:
                        item = datalab_client.get_item(refcode=entry["refcode"])
                        if item:
                            entry["item_id"] = entry["refcode"].split(":")[1]
                            print(f"Skipping existing entry {entry['item_id']}")
                    except Exception:
                        pass

                # In a previous life, cheminventory sync used barcodes or randomly created IDs when
                # syncing to datalab. We need to also detect this scenario; i.e., if the item has a barcode,
                # does it match an existing barcoded entry in datalab?
                if entry.get("barcode"):
                    ids_found.add(str(entry["barcode"]))
                    try:
                        item = datalab_client.get_item(item_id=entry["barcode"])
                        if item:
                            entry["item_id"] = entry["barcode"]
                            print(f"Skipping existing entry {entry['item_id']}")
                    except Exception:
                        pass

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
                                f"[green]✓\t{entry.get('item_id')}\t{entry.get('barcode')}[/green]"
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
                                f"[yellow]·\t{entry.get('item_id')}\t{entry.get('barcode')}[/yellow]"
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
                                        f"[green]✓\tAdded file to {entry.get('item_id')}\t{entry.get('barcode')}[/green]"
                                    )

                    except Exception as e:
                        failures += 1
                        pprint(
                            f"[red]✗\t{entry.get('item_id')}\t{entry.get('barcode')}:\n{e}[/red]"
                        )

            if not dry_run:
                pprint(f"\n[green]Created {successes} items.[/green]")
                if updated > 0:
                    pprint(f"[yellow]Updated {updated} items.[/yellow]")
                if failures > 0:
                    pprint(f"[red]Failed to create {failures} items.[/red]")

            if dry_run:
                pprint(f"\n[green]Found {total} items.[/green]")

            for row in rich.progress.track(
                self.get_deleted_containers(), description="Checking deleted containers"
            ):
                # If the item already exists, pull it and see if it needs to be updated -- need to check both ID and barcode as before
                container_id = row.get("id")
                barcode = row.get("barcode")
                existing_item = None
                found_id = None
                try:
                    existing_item = datalab_client.get_item(barcode)
                    found_id = barcode
                except Exception:
                    pass

                if not existing_item:
                    try:
                        existing_item = datalab_client.get_item(container_id)
                        found_id = container_id
                    except Exception:
                        pass

                if existing_item and found_id:
                    item_data = {"status": "disposed"}

                    if not dry_run:
                        response = datalab_client.update_item(
                            found_id,
                            item_data,
                        )

                    pprint(
                        f"[green]Disposed datalab item {container_id} as it was deleted in cheminventory.[/green]"
                    )

                else:
                    pprint(
                        f"[yellow]Could not find deleted container {container_id} in datalab.[/yellow]"
                    )

        return ids_found, ids_deleted


def _main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Sync from cheminventory to datalab and back.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not create items in datalab.",
    )
    parser.add_argument(
        "--skip-files",
        action="store_true",
        help="Do not download files from cheminventory.",
    )
    parser.add_argument(
        "--c2d-only",
        action="store_true",
        help="Only sync from cheminventory to datalab.",
    )
    args = parser.parse_args()
    syncer = ChemInventoryDatalabSyncer(
        dry_run=args.dry_run, skip_files=args.skip_files, c2d_only=args.c2d_only
    )
    syncer.sync()


if __name__ == "__main__":
    _main()
