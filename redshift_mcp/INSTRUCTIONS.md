# Talk-to-Redshift — Assistant Instructions

## Database structure

- There is one database per client.
- The data is pulled by Fivetran and transformed using dbt. The assistant doesn't have access to these resources.
- Each database has a `reporting` schema and other intermediary schemas that are usually not used to build Metabase resources.
- The resources used are generally the ones from the `reporting` schemas and sometimes `gsheet_raw` and `s3_raw`.
- The `reporting` schema contains the most used tables, that can be platform-specific or blended. The assistant MUST ONLY use `reporting`, `gsheet_raw` and `s3_raw` tables.
- If a `blended_performance` or `blended` table is available in the `reporting` schema it should be used in priority instead of blending data from other tables.

## Table usage

- Before using a table the assistant should always call `list_columns` to understand what it contains.
- Most reporting tables have a `date_granularity` field that usually takes the values `day`, `week`, `month`, `quarter`, `year`. This granularity means the row contains aggregations corresponding to the date field of the row.
- In particular: for some clients the weeks don't start on Mondays. When requested for weekly data, the assistant should take advantage of this custom date granularity.
- When a table has a `date_granularity` field, it MUST ALWAYS be filtered on exactly one value in order to have coherent results.

## Workflow

- If the target client database is not already known, always call `list_databases` first.
- Always call `list_tables` on the `reporting` schema before writing any query to know what tables are available.
- Always call `list_columns` on a table before querying it to understand its structure and available fields.
