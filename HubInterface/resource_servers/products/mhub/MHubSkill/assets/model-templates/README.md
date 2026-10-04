# Scaffolded MHub Model

This directory is the starting point for a compliant MHub model package.

## Important files

- `meta.json` describes the model metadata following the schema at https://mhub.ai/docs/reference/meta
- `config/default.yml` defines the default workflow; see `references/nifti-workflows.md` for importer/organizer syntax
- `Dockerfile` follows the mhub.io Docker guidelines (base image, entrypoint, `uv pip install`)

## Setup

1. Update the placeholders (`$model_id`, `$label`, etc.) to match your model.
2. Adjust `config/default.yml` to reference the correct runner module and outputs.
3. Build the container:
    ```bash
    docker build -t mhubai/$model_id:latest .
    ```
4. Push to your registry or run locally with mhub helper workflows.
