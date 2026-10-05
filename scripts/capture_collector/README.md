# Capture real Collector requests

Records what an OpenTelemetry Collector `otlphttp` exporter sends, for the fixtures in
`tests/fixtures/collector_real/`. Needs Docker and Python 3.11+. On Linux, replace
`host.docker.internal` in `collector.yaml` with the host address (or add
`--add-host=host.docker.internal:host-gateway` to `docker run`).

1. Start the recorder: `python3 scripts/capture_collector/record.py out`
2. Start the Collector (pin the image by digest, as in the fixtures' `SOURCE.txt`):

       docker run -d --name collector-capture -p 4318:4318 \
         -v "$PWD/scripts/capture_collector/collector.yaml:/etc/otelcol-contrib/config.yaml:ro" \
         otel/opentelemetry-collector-contrib@sha256:<digest>

3. Send a demo trace document (one line of a file in `src/detecttrace/demo_data/traces/`):

       gzip -dc src/detecttrace/demo_data/traces/traces.jsonl.gz | sed -n 1p > line.json
       curl -H 'Content-Type: application/json' --data-binary @line.json http://localhost:4318/v1/traces

4. Wait a few seconds for the batch to flush. `out/` now holds `NNN_json.*` and
   `NNN_proto.*` files: a `.body` (raw bytes) and a `.json` (method, path, headers; the
   `Authorization` value is redacted).
5. To keep a request as a fixture, copy its `.body` under a name that says its format
   (`name.json.gz` for a gzipped JSON body, `name.pb.gz` for gzipped protobuf) and its `.json`
   as `name.headers.json`. The fixture tests decode files by suffix.
6. Clean up: `docker rm -f collector-capture`, then stop the recorder.
