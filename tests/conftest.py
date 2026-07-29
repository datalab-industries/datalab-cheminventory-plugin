import copy
import json

import respx
from datalab_api import DuplicateItemError
from httpx import Response
from pytest import fixture


@fixture
def cheminventory_api_url():
    return "https://example.cheminventory.net"


@fixture
def example_locations():
    return [
        {
            "id": 927143,
            "name": "[Unassigned]",
            "barcode": None,
            "parent": 927135,
            "numbercontainers": 3,
        },
        {"id": 927154, "name": "4_007", "barcode": None, "parent": 927135, "numbercontainers": 0},
        {"id": 927155, "name": "4_008", "barcode": None, "parent": 927135, "numbercontainers": 0},
        {"id": 944121, "name": "Acids", "barcode": None, "parent": 927154, "numbercontainers": 0},
        {"id": 944122, "name": "Bases", "barcode": None, "parent": 927154, "numbercontainers": 0},
        {
            "id": 944119,
            "name": "Chemical Cupboard",
            "barcode": None,
            "parent": 927154,
            "numbercontainers": 185,
        },
        {
            "id": 927138,
            "name": "Desiccator",
            "barcode": None,
            "parent": 927154,
            "numbercontainers": 3,
        },
        {
            "id": 927140,
            "name": "Disposed",
            "barcode": None,
            "parent": 927135,
            "numbercontainers": 1,
        },
        {"id": 927135, "name": "FIHM Group", "barcode": None, "parent": 0, "numbercontainers": 0},
        {
            "id": 944120,
            "name": "Flammables",
            "barcode": None,
            "parent": 927154,
            "numbercontainers": 0,
        },
        {"id": 927136, "name": "Fridge", "barcode": None, "parent": 927154, "numbercontainers": 3},
        {
            "id": 927139,
            "name": "Glovebox",
            "barcode": None,
            "parent": 927154,
            "numbercontainers": 36,
        },
        {
            "id": 932359,
            "name": "Nottingham",
            "barcode": None,
            "parent": 927135,
            "numbercontainers": 0,
        },
        {
            "id": 944123,
            "name": "Oxidisers",
            "barcode": None,
            "parent": 927154,
            "numbercontainers": 0,
        },
        {
            "id": 927599,
            "name": "Solvent cabinet",
            "barcode": None,
            "parent": 927154,
            "numbercontainers": 16,
        },
    ]


@fixture
def mocked_cheminventory_api(example_locations, mock_environ, cheminventory_api_url):
    with respx.mock(base_url=cheminventory_api_url, assert_all_called=False) as respx_mock:
        fake_getdetails = respx_mock.post("/general/getdetails")
        fake_getdetails.return_value = Response(
            200,
            json={
                "status": "success",
                "data": {
                    "user": {
                        "id": 101,
                        "email": "test@example.com",
                        "inventory": 1,
                        "inventoryname": "Example",
                        "otherInventories": [],
                    },
                    "inventory": {"id": 1, "name": "Example"},
                },
            },
        )

        fake_locations = respx_mock.post("/location/load")
        fake_locations.return_value = Response(
            200, json={"status": "success", "data": example_locations}
        )

        yield respx_mock


@fixture
def mock_environ(monkeypatch, cheminventory_api_url):
    monkeypatch.setenv("CHEMINVENTORY_API_URL", cheminventory_api_url)
    monkeypatch.setenv("CHEMINVENTORY_API_KEY", "test")
    monkeypatch.setenv("DATALAB_API_URL", "https://example.datalab.com")


class FakeDatalab:
    """A minimal in-memory stand-in for `datalab_api.DatalabClient`.

    The instance itself is patched in as the `DatalabClient` class: calling it
    (i.e., "constructing a client") returns the same shared instance, so state
    persists across multiple syncs in a test.
    """

    def __init__(self):
        self.items: dict[str, dict] = {}
        self._refcode_counter = 0

    def __call__(self, api_url, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def _new_refcode(self) -> str:
        """Refcode suffixes are random in datalab and carry no relation to the
        item_id (e.g. a real pair is item_id 'jdb-1-1' / refcode 'grey:AIZYJI').
        """
        self._refcode_counter += 1
        return f"test:QQ{self._refcode_counter:04d}"

    def seed_item(self, item_id: str, **fields) -> dict:
        """Seed a datalab-native starting material."""
        item = {
            "item_id": item_id,
            "type": "starting_materials",
            "refcode": self._new_refcode(),
            "status": "available",
            "files": [],
        }
        item.update(fields)
        self.items[str(item_id)] = item
        return item

    def get_items(self, item_type="samples"):
        return [copy.deepcopy(i) for i in self.items.values() if i["type"] == item_type]

    def get_item(self, item_id=None, refcode=None, load_blocks=False):
        if item_id is None and refcode is None:
            raise ValueError("Must provide one of `item_id` or `refcode`.")
        if refcode is not None:
            for item in self.items.values():
                if item.get("refcode") == refcode:
                    return copy.deepcopy(item)
            raise RuntimeError(f"Failed to find item {refcode=}.")
        item = self.items.get(str(item_id))
        if item is None:
            raise RuntimeError(f"Failed to find item {item_id=}.")
        return copy.deepcopy(item)

    def create_item(self, item_id=None, item_type="samples", item_data=None, collection_id=None):
        if str(item_id) in self.items:
            raise DuplicateItemError(f"Item {item_id=} already exists.")
        item = dict(item_data or {})
        item.update({"item_id": item_id, "type": item_type})
        # datalab assigns its own refcode on creation, dropping any supplied one
        item["refcode"] = self._new_refcode()
        item.setdefault("files", [])
        self.items[str(item_id)] = item
        return copy.deepcopy(item)

    def update_item(self, item_id, item_data):
        item = self.items.get(str(item_id))
        if item is None:
            raise RuntimeError(f"Failed to find item {item_id=}.")
        item.update(item_data)
        return {"status": "success"}


class FakeChemInventory:
    """Stateful fake of the cheminventory backend, exposed through respx routes
    by the `fake_cheminventory` fixture so that containers added during a sync
    show up in subsequent inventory exports.
    """

    inventory_id = 1
    inventory_name = "Example"

    def __init__(self, locations):
        self.locations = locations
        self.rows: list[dict] = []
        self.deleted: list[dict] = []
        self.container_fields: list[dict] = []
        self.substance_fields: list[dict] = []
        self.added_containers: list[dict] = []
        self._next_row_id = 1000
        self._next_field_id = 1

    def add_row(self, **overrides) -> dict:
        """Add a container row, matching the shape of a real
        /inventorymanagement/export response row.
        """
        row_id = overrides.pop("id", None)
        if row_id is None:
            row_id = self._next_row_id
            self._next_row_id += 1
        row = {
            "id": row_id,
            "barcode": "",
            "name": "Unknown",
            "size": "100",
            "unit": "g",
            "supplier": "",
            "cas": "",
            "hcodes": None,
            "smiles": "",
            "molecularformula": "",
            "molecularweight": "",
            "location": f"{self.inventory_name} > FIHM Group > 4_007 > Glovebox",
            "locationid": 927139,
            "dateacquired": "",
            "comments": "",
            "disposed": "0",
            "substanceid": 9001,
            "historycount": 0,
            "inventory": self.inventory_id,
            "inventoryname": self.inventory_name,
            "siteid": 1,
            "sitename": self.inventory_name,
            "lastuser": "A User",
            "usercreatedname": "A User",
        }
        row.update(overrides)
        self.rows.append(row)
        return row

    def delete_container(self, container_id: int) -> dict:
        """Move a container from the inventory to the deleted containers list,
        matching the shape of a real /inventorymanagement/deletedcontainers/get
        response entry.
        """
        row = next(r for r in self.rows if r["id"] == container_id)
        self.rows.remove(row)
        self.deleted.append(
            {
                "id": row["id"],
                "barcode": row["barcode"],
                "containername": row["name"],
                "datedeleted": "2026-07-11 12:00:00",
                "user": "A User",
            }
        )
        return row

    def custom_field_key(self, name: str) -> str | None:
        """Return the cf-/sf- prefixed key for a custom field name, if defined."""
        for field in self.container_fields:
            if field["name"] == name:
                return f"cf-{field['id']}"
        for field in self.substance_fields:
            if field["name"] == name:
                return f"sf-{field['id']}"
        return None

    def _location_string(self, location_id: int) -> str:
        by_id = {loc["id"]: loc for loc in self.locations}
        parts = []
        loc = by_id[location_id]
        while loc:
            parts.append(loc["name"])
            loc = by_id.get(loc.get("parent"))
        return " > ".join([self.inventory_name, *reversed(parts)])


def _success(data) -> Response:
    return Response(200, json={"status": "success", "data": data})


@fixture
def fake_cheminventory(example_locations, mock_environ, cheminventory_api_url):
    fake = FakeChemInventory(example_locations)

    with respx.mock(base_url=cheminventory_api_url, assert_all_called=False) as respx_mock:
        respx_mock.post("/general/getdetails").mock(
            side_effect=lambda request: _success(
                {
                    "user": {
                        "id": 101,
                        "email": "test@example.com",
                        "inventory": fake.inventory_id,
                        "inventoryname": fake.inventory_name,
                        "otherInventories": [],
                    },
                    "inventory": {"id": fake.inventory_id, "name": fake.inventory_name},
                }
            )
        )
        respx_mock.post("/location/load").mock(
            side_effect=lambda request: _success(copy.deepcopy(fake.locations))
        )
        respx_mock.post("/inventorymanagement/export").mock(
            side_effect=lambda request: _success({"rows": copy.deepcopy(fake.rows)})
        )
        respx_mock.post("/inventorymanagement/deletedcontainers/get").mock(
            side_effect=lambda request: _success(copy.deepcopy(fake.deleted))
        )
        respx_mock.post("/customfields/get").mock(
            side_effect=lambda request: _success(
                {"container": fake.container_fields, "substance": fake.substance_fields}
            )
        )

        def _save_custom_field(request):
            body = json.loads(request.content)
            target = (
                fake.substance_fields
                if body.get("fieldtype") == "substance"
                else fake.container_fields
            )
            target.append(
                {
                    "id": fake._next_field_id,
                    "name": body["name"],
                    "type": body.get("type", "text"),
                    "inventory": fake.inventory_id,
                    "site": 0,
                    "enterprise": 0,
                    "hidden": "0",
                    "searchable": 0,
                    "allowedvalues": "",
                    "showfororders": 0,
                    "admineditonly": 0,
                }
            )
            fake._next_field_id += 1
            return _success({})

        respx_mock.post("/customfields/save").mock(side_effect=_save_custom_field)

        def _add_container(request):
            body = json.loads(request.content)
            for container in body["data"]:
                fake.added_containers.append(container)
                row = fake.add_row(
                    name=container.get("name"),
                    barcode=container.get("barcode", ""),
                    substanceid=container.get("substanceid"),
                    dateacquired=container.get("dateacquired", ""),
                    location=fake._location_string(container["locationid"]),
                )
                for key, value in container.items():
                    if key.startswith(("cf-", "sf-")):
                        row[key] = value
            return _success({})

        respx_mock.post("/container/add").mock(side_effect=_add_container)

        def _delete_container(request):
            body = json.loads(request.content)
            for container_id in body["containerid"]:
                fake.delete_container(container_id)
            return _success({})

        respx_mock.post("/container/delete").mock(side_effect=_delete_container)
        respx_mock.post("/container/getsubstance").mock(
            side_effect=lambda request: _success([{"id": 9001}])
        )
        respx_mock.post("/filestore/getlinkedfiles").mock(side_effect=lambda request: _success([]))

        yield fake


@fixture
def fake_datalab(monkeypatch):
    import datalab_cheminventory_plugin

    fake = FakeDatalab()
    monkeypatch.setattr(datalab_cheminventory_plugin, "DatalabClient", fake)
    return fake


@fixture
def syncer(fake_cheminventory, fake_datalab):
    from datalab_cheminventory_plugin import ChemInventoryDatalabSyncer

    return ChemInventoryDatalabSyncer(skip_files=True)
