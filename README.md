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
export DATALAB_API_URL="https://example.datalab-org.io"
export DATALAB_API_KEY="xxx"
datalab-cheminventory-sync --dry-run
```
