"""End-to-end sync scenarios against a stateful fake cheminventory backend
(respx) and an in-memory fake datalab client (see conftest.py).
"""

import copy


def test_initial_sync_creates_datalab_items(syncer, fake_cheminventory, fake_datalab):
    fake_cheminventory.add_row(id=101, name="Lithium foil", cas="7439-93-2")
    fake_cheminventory.add_row(id=102, name="Sodium carbonate", cas="497-19-8", comments="99.5%")

    syncer.sync()

    assert set(fake_datalab.items) == {"101", "102"}
    item = fake_datalab.items["101"]
    assert item["Container Name"] == "Lithium foil"
    assert item["Substance CAS"] == "7439-93-2"
    assert item["status"] == "available"
    assert item["type"] == "starting_materials"
    assert fake_datalab.items["102"]["description"] == "99.5%"

    # Nothing should have been pushed back to cheminventory
    assert fake_cheminventory.added_containers == []
    assert len(fake_cheminventory.rows) == 2


def test_container_disposed_in_cheminventory_syncs_disposed_status(
    syncer, fake_cheminventory, fake_datalab
):
    fake_cheminventory.add_row(id=101, name="Old acid", disposed="1")

    syncer.sync()

    assert fake_datalab.items["101"]["status"] == "disposed"
    assert fake_cheminventory.added_containers == []


def test_double_sync_is_idempotent(syncer, fake_cheminventory, fake_datalab):
    fake_cheminventory.add_row(id=101, name="Lithium foil")
    fake_cheminventory.add_row(id=102, name="Sodium carbonate")

    syncer.sync()
    items_after_first = copy.deepcopy(fake_datalab.items)

    syncer.sync()

    assert fake_datalab.items == items_after_first
    assert fake_cheminventory.added_containers == []
    assert len(fake_cheminventory.rows) == 2


def test_cheminventory_edits_propagate_to_datalab(syncer, fake_cheminventory, fake_datalab):
    row = fake_cheminventory.add_row(id=101, name="Lithium foil")
    syncer.sync()

    row["name"] = "Lithium foil (0.1 mm)"
    row["comments"] = "Opened 2026-07-01"
    syncer.sync()

    assert len(fake_datalab.items) == 1
    item = fake_datalab.items["101"]
    assert item["Container Name"] == "Lithium foil (0.1 mm)"
    assert "Opened 2026-07-01" in item["description"]


def test_container_deleted_in_cheminventory_is_disposed_in_datalab(
    syncer, fake_cheminventory, fake_datalab
):
    fake_cheminventory.add_row(id=101, name="Lithium foil")
    fake_cheminventory.add_row(id=102, name="Sodium carbonate")
    syncer.sync()
    assert fake_datalab.items["101"]["status"] == "available"

    fake_cheminventory.delete_container(101)
    syncer.sync()

    assert fake_datalab.items["101"]["status"] == "disposed"
    assert fake_datalab.items["102"]["status"] == "available"


def test_deleted_container_is_not_readded_to_cheminventory(
    syncer, fake_cheminventory, fake_datalab
):
    fake_cheminventory.add_row(id=101, name="Lithium foil")
    syncer.sync()

    fake_cheminventory.delete_container(101)
    syncer.sync()
    assert fake_datalab.items["101"]["status"] == "disposed"

    # Further syncs must not resurrect the container in cheminventory
    syncer.sync()
    assert fake_cheminventory.added_containers == []
    assert fake_cheminventory.rows == []
    assert fake_datalab.items["101"]["status"] == "disposed"


def test_item_disposed_in_datalab_is_not_synced_to_cheminventory(
    syncer, fake_cheminventory, fake_datalab
):
    """Regression test: an item marked as disposed in datalab (e.g. manually)
    must not be created as a new cheminventory container, even though it does
    not appear in the cheminventory deleted containers list.
    """
    fake_datalab.seed_item(
        "old_sample",
        name="Legacy powder",
        status="disposed",
        location="Example > FIHM Group > 4_007 > Glovebox",
    )

    syncer.sync()

    assert fake_cheminventory.added_containers == []
    assert fake_cheminventory.rows == []


def test_datalab_native_item_synced_to_cheminventory_once(syncer, fake_cheminventory, fake_datalab):
    seeded = fake_datalab.seed_item(
        "mp_0001",
        name="Novel electrolyte",
        location="Example > FIHM Group > 4_007 > Glovebox",
        date="2026-06-01",
    )

    syncer.sync()

    assert len(fake_cheminventory.added_containers) == 1
    container = fake_cheminventory.added_containers[0]
    assert container["name"] == "Novel electrolyte"
    assert container["locationid"] == 927139
    assert container["dateacquired"] == "2026-06-01"
    assert container["barcode"] == "mp_0001"

    # The DataLab ID custom field was created and carries the refcode
    field_key = fake_cheminventory.custom_field_key("DataLab ID")
    assert field_key is not None
    assert container[field_key] == seeded["refcode"]

    # Second sync: the labelled container round-trips back to the same datalab
    # item, with no duplicate item and no second container
    syncer.sync()

    assert len(fake_cheminventory.added_containers) == 1
    assert len(fake_cheminventory.rows) == 1
    assert set(fake_datalab.items) == {"mp_0001"}


def test_legacy_labelled_container_matches_datalab_item_by_refcode(
    syncer, fake_cheminventory, fake_datalab
):
    """A container labelled with a DataLab ID refcode but no barcode (as created
    by older plugin versions) must be matched back to the datalab item whose
    refcode it carries, not spawn a duplicate item named after the refcode
    suffix (which is random and unrelated to the item_id).
    """
    seeded = fake_datalab.seed_item(
        "jdb-1-1",
        name="Legacy electrolyte",
        location="Example > FIHM Group > 4_007 > Glovebox",
    )
    # Field IDs must exist before rows can carry cf- values, matching a real
    # inventory where the field was created by a previous sync
    fake_cheminventory.container_fields.append({"id": 11192, "name": "DataLab ID"})
    fake_cheminventory.add_row(id=301, name="Legacy electrolyte", **{"cf-11192": seeded["refcode"]})

    syncer.sync()
    syncer.sync()

    assert set(fake_datalab.items) == {"jdb-1-1"}
    assert fake_datalab.items["jdb-1-1"]["Container Name"] == "Legacy electrolyte"
    # the matched container must also not be pushed back as a new container
    assert fake_cheminventory.added_containers == []


def test_barcode_match_prevents_duplicate_datalab_items(syncer, fake_cheminventory, fake_datalab):
    """A cheminventory container whose barcode matches an existing datalab
    item_id should update that item rather than create a duplicate, and the
    datalab item should not be pushed back as a new container.
    """
    fake_datalab.seed_item(
        "LAB-0042",
        name="Barcode-labelled powder",
        location="Example > FIHM Group > 4_007 > Glovebox",
    )
    fake_cheminventory.add_row(id=201, name="Barcode-labelled powder", barcode="LAB-0042")

    syncer.sync()

    assert set(fake_datalab.items) == {"LAB-0042"}
    assert fake_datalab.items["LAB-0042"]["Container Name"] == "Barcode-labelled powder"
    assert fake_cheminventory.added_containers == []


def test_disposing_cheminventory_origin_item_in_datalab_deletes_container(
    syncer, fake_cheminventory, fake_datalab
):
    """Marking a cheminventory-origin item as disposed in datalab must delete
    the linked container in cheminventory, not be reverted to 'available' by
    the next sync.
    """
    fake_cheminventory.add_row(id=101, name="Lithium foil")
    syncer.sync()
    assert fake_datalab.items["101"]["status"] == "available"

    fake_datalab.items["101"]["status"] = "disposed"
    syncer.sync()

    assert fake_cheminventory.rows == []
    assert fake_cheminventory.deleted[0]["id"] == 101
    assert fake_cheminventory.deleted[0]["containername"] == "Lithium foil"
    assert fake_datalab.items["101"]["status"] == "disposed"

    # Further syncs converge: nothing is re-added or revived on either side
    syncer.sync()
    assert fake_cheminventory.rows == []
    assert fake_cheminventory.added_containers == []
    assert fake_datalab.items["101"]["status"] == "disposed"


def test_disposing_datalab_native_item_deletes_container(syncer, fake_cheminventory, fake_datalab):
    """Disposing a datalab-native item after it has been synced to
    cheminventory must delete its container there (matched via barcode) and
    not re-add it on later syncs.
    """
    fake_datalab.seed_item(
        "mp_0001",
        name="Novel electrolyte",
        location="Example > FIHM Group > 4_007 > Glovebox",
    )
    syncer.sync()
    assert len(fake_cheminventory.rows) == 1

    fake_datalab.items["mp_0001"]["status"] = "disposed"
    syncer.sync()

    assert fake_cheminventory.rows == []
    assert fake_datalab.items["mp_0001"]["status"] == "disposed"

    syncer.sync()
    assert len(fake_cheminventory.added_containers) == 1
    assert fake_cheminventory.rows == []


def test_datalab_native_item_deleted_in_cheminventory_is_disposed(
    syncer, fake_cheminventory, fake_datalab
):
    """A datalab-native item's container carries the datalab item_id as its
    barcode, so deleting it in cheminventory must dispose the datalab item
    and prevent it from being re-added on later syncs.
    """
    fake_datalab.seed_item(
        "mp_0001",
        name="Novel electrolyte",
        location="Example > FIHM Group > 4_007 > Glovebox",
    )
    syncer.sync()
    assert len(fake_cheminventory.rows) == 1

    fake_cheminventory.delete_container(fake_cheminventory.rows[0]["id"])
    syncer.sync()

    assert fake_datalab.items["mp_0001"]["status"] == "disposed"

    syncer.sync()
    assert len(fake_cheminventory.added_containers) == 1
    assert fake_cheminventory.rows == []
