import respx
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
