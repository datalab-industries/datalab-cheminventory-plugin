import pytest


def test_location_hierarchy(mocked_cheminventory_api, example_locations):
    from datalab_cheminventory_plugin import ChemInventoryDatalabSyncer

    syncer = ChemInventoryDatalabSyncer()
    syncer.construct_locations_hierarchy()
    locs_by_name = {loc["full_name"]: loc for loc in syncer._locations}
    assert locs_by_name["FIHM Group > 4_007 > Chemical Cupboard"]["id"] == 944119
    assert locs_by_name["FIHM Group > 4_007 > Chemical Cupboard"]["numbercontainers"] == 185
    assert locs_by_name["FIHM Group > Nottingham"]["id"] == 932359
    assert locs_by_name["FIHM Group > Nottingham"]["numbercontainers"] == 0

    assert len(locs_by_name) == len(example_locations)


def test_location_resolver(mocked_cheminventory_api):
    from datalab_cheminventory_plugin import ChemInventoryDatalabSyncer

    syncer = ChemInventoryDatalabSyncer.__new__(ChemInventoryDatalabSyncer)
    syncer.construct_locations_hierarchy()
    assert syncer.get_location_id("FIHM Group > FIHM Group > 4_007 > Chemical Cupboard") == 944119
    assert syncer.get_location_id("FIHM Group > FIHM Group > Nottingham") == 932359
    with pytest.raises(ValueError, match="No location.*"):
        syncer.get_location_id("FIHM Group > 4_007 > Chemical Cupboard")
