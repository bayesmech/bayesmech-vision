Get the data files we need for analysis.

```sh
aws s3 sync s3://bayesmech-recordings/recordings/ ./recordings/ --exclude "*" --include "*.vis.pb"
```

