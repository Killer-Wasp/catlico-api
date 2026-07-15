# Attachment storage providers

Blob storage (file attachments and file observables) is a thin
[fsspec](https://filesystem-spec.readthedocs.io/) abstraction in
`app/core/storage.py`. Blobs are content-addressed by SHA-256, immutable, and
never deleted, so the same code path targets a local directory, an
S3-compatible store, GCS, or Azure Blob — you only change `STORAGE_PROTOCOL`
(and install the matching fsspec adapter for GCS/Azure).

Set the provider with `STORAGE_PROTOCOL` and the container with `STORAGE_ROOT`
(a directory for `local`; a bucket, optionally `bucket/prefix`, for object
stores). See `docs/configuration.md` for every knob.

## What ships by default

The base install (`pip install catlico-backend` / `uv sync`) includes:

| protocol | adapter | notes |
|----------|---------|-------|
| `local`  | built-in | a directory on disk (dev / single-node only — per-replica) |
| `s3`     | `s3fs`  | AWS S3 **and** any S3-compatible store (SeaweedFS, MinIO) |

### S3-compatible stores (SeaweedFS / MinIO)

No extra install — point the base S3 adapter at the alternate endpoint. This is
exactly how `docker-compose.yml` runs against SeaweedFS:

```bash
STORAGE_PROTOCOL=s3
STORAGE_ROOT=catlico                     # bucket (create it first)
S3_ENDPOINT_URL=http://minio:9000        # or http://seaweedfs:8333
S3_ACCESS_KEY=...
S3_SECRET_KEY=...
S3_REGION=us-east-1                       # any value; required by the SDK
```

Leave `S3_ENDPOINT_URL` empty for real AWS S3 (the SDK resolves the regional
endpoint from `S3_REGION`).

## Optional providers (extras)

GCS and Azure need their fsspec adapter, packaged as optional extras
(Phase 6 §6.4) so the base image stays lean:

```bash
pip install "catlico-backend[gcs]"          # -> gcsfs
pip install "catlico-backend[azure]"        # -> adlfs
# or, in this repo:
uv sync --extra gcs --extra azure
```

### Google Cloud Storage — `STORAGE_PROTOCOL=gcs`

```bash
STORAGE_PROTOCOL=gcs
STORAGE_ROOT=my-gcs-bucket
# Credentials are ambient (Application Default Credentials):
GOOGLE_APPLICATION_CREDENTIALS=/secrets/gcp-sa.json   # or workload identity
```

### Azure Blob Storage — `STORAGE_PROTOCOL=az` (alias `abfs`)

```bash
STORAGE_PROTOCOL=az
STORAGE_ROOT=my-container
# Credentials are ambient (read by adlfs):
AZURE_STORAGE_CONNECTION_STRING=...
# or AZURE_STORAGE_ACCOUNT_NAME + a managed identity / AZURE_STORAGE_ACCOUNT_KEY
```

`gcs`/`az` credentials are read from the environment by the adapter, not from
the `S3_*` settings (those apply only to `STORAGE_PROTOCOL=s3`).

## Testing / CI follow-up

A storage smoke-test matrix (exercise `put`/`exists`/`stream`/`delete` against
`local` and a MinIO service container in CI, with `gcs`/`az` gated behind
credentials) is a **documented CI follow-up** — the existing suite already
covers S3 via `moto`. See Phase 6 §6.4.
