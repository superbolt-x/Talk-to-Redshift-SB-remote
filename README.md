# Talk-to-Redshift MCP

Read-only Redshift MCP server for Claude. Deploy on Railway and connect your whole team via a single URL.

## Tools

| Tool | Description |
|---|---|
| `list_clusters` | List provisioned clusters and serverless workgroups |
| `list_databases` | List databases in the cluster |
| `list_schemas` | List schemas in a database |
| `list_tables` | List tables in a schema |
| `list_columns` | List columns and types for a table |
| `execute_query` | Run a SELECT query (writes blocked) |

## Railway deployment

### 1. Fork / clone this repo and push to GitHub

### 2. Create a new Railway project from the repo

### 3. Set environment variables in Railway

| Variable | Description |
|---|---|
| `AWS_ACCESS_KEY_ID` | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key |
| `AWS_DEFAULT_REGION` | e.g. `us-east-1` |
| `REDSHIFT_CLUSTER_ID` | Cluster identifier (provisioned) **or** |
| `REDSHIFT_WORKGROUP` | Workgroup name (serverless) |
| `REDSHIFT_DB_USER` | DB user for provisioned clusters |
| `SERVER_URL` | Your Railway public URL, e.g. `https://your-app.railway.app` |
| `MCP_AUTH_TOKEN` | Passphrase your team uses to authorize Claude |

### 4. Connect Claude

In Claude.ai → Settings → Integrations → Add MCP Server:
```
https://your-app.railway.app/mcp
```

Each team member enters the `MCP_AUTH_TOKEN` passphrase once in their browser.

## IAM permissions required

```json
{
  "Effect": "Allow",
  "Action": [
    "redshift-data:ExecuteStatement",
    "redshift-data:GetStatementResult",
    "redshift-data:DescribeStatement",
    "redshift:DescribeClusters",
    "redshift:GetClusterCredentials",
    "redshift-serverless:ListWorkgroups"
  ],
  "Resource": "*"
}
```

## Local development

```bash
pip install -e .

export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-east-1
export REDSHIFT_CLUSTER_ID=my-cluster
export REDSHIFT_DB_USER=myuser

python -m redshift_mcp
```
