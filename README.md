# BayesMech Vision

## New machine setup

1. Clone the repo.

2. Copy `.env.example` to `.env` and fill in the required keys. If another
   worktree on the same machine already has a usable `.env`, symlink to that
   file instead.

3. Create or clone the `recordings/` directory. The full recordings bucket can
   be synced from S3:

   ```sh
   aws s3 sync s3://bayesmech-recordings/recordings/ ./recordings/
   ```

   If another local worktree already has `recordings/`, symlink to that
   directory instead.

4. Download model weights from the `bayesmech-models` S3 bucket into
   `server/segmentation/models`, or symlink that directory from another local
   worktree.

5. Install the Python environment:

   ```sh
   cd server
   uv sync
   ```

6. Install the dashboard dependencies:

   ```sh
   cd analysis/dashboard
   npm install
   ```

## Running on EC2

Run the dashboard server:

```sh
cd analysis/dashboard
npm run devserver
```

Run the streamlog server separately:

```sh
cd server/streamlog
cargo run
```

## Analyzer commands

When running any Python-based analyzer, always run it from `server/` because the
`uv` environment configuration is there.

## Recording data

Get the `.vis.pb` data files needed for analysis:

```sh
aws s3 sync s3://bayesmech-recordings/recordings/ ./recordings/ --exclude "*" --include "*.vis.pb"
```
