# <div align="center"><i>datalab</i> ChemInventory Plugin</div>

A plugin that enables two-way syncing between *datalab* instances and [cheminventory.net](https://www.cheminventory.net/).

## Installation

This plugin can be installed using [`uv`](https://astral.sh/uv), via:

```bash
git clone git@github.com:datalab-industries/datalab-cheminventory-plugin
cd datalab-cheminventory-plugin
uv sync
```

## Usage

This plugin can be run on a schedule from a datalab server, or as a user after
setting the relevant environment variables for the *datalab* instance and
cheminventory.
You can find additional documentation for cheminventory [API authentication](https://www.cheminventory.net/support/api/#apiauthentication)
and for [datalab API authentication](https://api-docs.datalab-org.io/en/stable/#authentication).

```bash
export CHEMINVENTORY_API_KEY="xxx"
export CHEMINVENTORY_INVENTORY_ID="12345"
export DATALAB_API_URL="https://example.datalab-org.io"
export DATALAB_API_KEY="xxx"
datalab-cheminventory-sync --dry-run
```

### Choosing an inventory

A cheminventory API key may have access to more than one inventory, so the inventory to sync should be set explicitly, either with `CHEMINVENTORY_INVENTORY_ID` or the `--inventory` flag.
If neither is set, the key uses the last inventory you viewed in the app, and can cause unexpected results if you have access to multiple inventories.

To find the numeric ID of each inventory the key can access, run the `status` command (no *datalab* connection is required):

```bash
export CHEMINVENTORY_API_KEY="xxx"
datalab-cheminventory-sync status
```

This prints the key's default inventory and any others it can access, each with its ID in brackets, e.g., `Default inventory: My Lab (12345)`, followed by a summary of the inventory that would be synced.

### Ansible role

An Ansible role using this plugin is available as part of the [datalab-ansible-terraform](https://github.com/datalab-industries/datalab-ansible-terraform) repository, which can be used to automate the install and configuration of a datalab server with this cheminventory plugin.
