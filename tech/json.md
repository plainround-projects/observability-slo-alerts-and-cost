# JSON

## What it is

JSON (JavaScript Object Notation) is a text format for data. It has objects (`{"key": value}`), arrays (`[1, 2, 3]`), strings in double quotes, numbers, `true`, `false` and `null`. It is the common language that programs use to talk over HTTP, and it is also the file format of Grafana dashboards. JSON has no comments and does not allow a trailing comma.

## Why this repository uses it

JSON appears at six levels here: the **dashboards** (Grafana's native format), the **checkout request and answer**, the **pager's webhook and its records** (JSON Lines), the **log lines**, the **Tempo and Loki APIs** the lab reads, and the **OTLP** documents posted in the redaction test.

## Where it appears

| Where | What |
| --- | --- |
| `observability/grafana/dashboards/slo-overview.json`, `replayed-week.json` | The dashboards (every field is explained in `grafana.md`). |
| `src/slo_lab/interfaces/service_app.py` | The request body and the answers. |
| `src/slo_lab/interfaces/pager_app.py`, `infrastructure/pager_store.py` | The Alertmanager webhook and the pages file. |
| `src/slo_lab/infrastructure/logging_setup.py` | One JSON object per log line. |
| `src/slo_lab/lab/replay.py` | `pages_to_lines` and `pages_from_lines`. |
| `tests/integration/test_redaction_in_the_collector.py` | OTLP/JSON documents. |

## How it works underneath

A program that sends JSON turns an in-memory structure into text (**serializing**, `json.dumps` in Python). A program that receives it turns the text back into a structure (**parsing**, `json.loads`). Python maps JSON objects to `dict`, arrays to `list`, strings to `str`, numbers to `int` or `float`, `true`/`false` to `True`/`False` and `null` to `None`.

Keys of an object are always strings. Numbers have no size limit in the format, but some programs store them as 64-bit floats, so very large integers can lose precision. That is one reason ids that are long (such as the 128-bit trace id) are sent as hexadecimal strings.

**Structured logging** writes one JSON object per line ("JSON Lines"). Each line is self-describing, so a log store can search by field.

## Walkthrough of this repository's use

**The checkout request.** The traffic generator builds the body from a dict:

```python
def build_body(kind: str, index: int) -> dict[str, object]:
    """The JSON body for one request of the given kind."""
    body: dict[str, object] = {"items": [{"sku": f"SKU-{index % 40:03d}", "quantity": 1 + index % 3}]}
    if kind == "personal":
        body.update(customer_email=TEST_EMAIL, card_number=TEST_CARD)
    if kind == "error":
        body["simulate"] = "error"
    if kind == "slow":
        body["simulate"] = "slow"
    return body
```

`{"items": [{"sku": "SKU-003", "quantity": 1}]}`, plus `customer_email` and `card_number` for a personal request and `simulate` for a planted error or slow request.

**The pager's records (JSON Lines).** One object per line:

```python
    def add(self, page: Page, received_at: float) -> None:
        """Append a page with the time it arrived (Unix seconds)."""
        record = {
            "alert": page.alert,
            "severity": page.severity,
            "offset_seconds": page.offset.total_seconds(),
            "received_at": received_at,
        }
        with self._lock, self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
```

`json.dumps(record) + "\n"` writes the line; `json.loads(line)` reads one back.

```python
def pages_to_lines(pages: list[Page]) -> str:
    """The pager log format: one JSON object per line."""
    return "".join(
        json.dumps({"alert": p.alert, "severity": p.severity, "offset_seconds": p.offset.total_seconds()}) + "\n"
        for p in pages
    )


def pages_from_lines(text: str) -> list[Page]:
    """Parse a pager log written by ``pages_to_lines``."""
    pages = []
    for line in text.splitlines():
        if line.strip():
            record = json.loads(line)
            pages.append(Page(record["alert"], record["severity"], timedelta(seconds=record["offset_seconds"])))
    return pages
```

The replay's pager log uses the same format with `offset_seconds`.

**An OTLP log record as JSON.** The redaction test posts documents the Collector's OTLP/HTTP receiver accepts:

```python
        now = str(time.time_ns())
        body = {
            "resourceLogs": [{"resource": {"attributes": []}, "scopeLogs": [{"logRecords": [{
                "timeUnixNano": now,
                "body": {"stringValue": json.dumps({"msg": f"payment from {TEST_EMAIL} with card {SPACED_CARD} ok"})},
                "attributes": [{"key": "note", "value": {"stringValue": f"card={TEST_CARD} mail={TEST_EMAIL}"}}],
            }]}]}]
        }  # fmt: skip
        span = {
            "resourceSpans": [{"resource": {"attributes": []}, "scopeSpans": [{"spans": [{
                "traceId": "0af7651916cd43dd8448eb211c80319c", "spanId": "b7ad6b7169203331", "name": "POST /checkout",
                "kind": 2, "startTimeUnixNano": now, "endTimeUnixNano": now,
                "attributes": [
                    {"key": "customer.email", "value": {"stringValue": TEST_EMAIL}},
                    {"key": "payment.card", "value": {"stringValue": TEST_CARD}},
                    {"key": "order.ref", "value": {"stringValue": "ord-000042"}},
                ],
            }]}]}]
        }  # fmt: skip
        assert requests.post(f"http://127.0.0.1:{port}/v1/logs", json=body, timeout=180).status_code == 200
```

`resourceLogs` → `scopeLogs` → `logRecords`, each with `timeUnixNano`, a `body` (`{"stringValue": ...}`; OTLP values are tagged by type) and `attributes` (a list of `{"key": ..., "value": {...}}`). The span document has `traceId` (32 hex digits), `spanId` (16), `kind`, the times and attributes. The body of the log record is itself a JSON string produced with `json.dumps`.

**A log line.** The formatter builds a dict and writes it with `json.dumps`:

```python
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "msg": record.getMessage(),
            "logger": record.name,
            "service": self._service_name,
        }
```

**The dashboards.** In a JSON string every `"` is `\"`, so a PromQL label matcher looks like this:

```json
          "expr": "1 - (sum(rate(http_requests_total{route!=\"/healthz\",status!~\"5..\"}[1h]))) / (sum(rate(http_requests_total{route!=\"/healthz\"}[1h])))",
```

`{route!=\"/healthz\",status!~\"5..\"}` is the text `{route!="/healthz",status!~"5.."}`.

## Try it yourself

```bash
python3 -m json.tool observability/grafana/dashboards/replayed-week.json | head -8
```

Expected: the file pretty-printed.

```bash
curl -s -X POST http://127.0.0.1:28083/checkout -H 'Content-Type: application/json' -d '{"items":[{"sku":"SKU-001","quantity":1}]}'
```

Expected (needs `make up`): `{"items":1,"reference":"ord-0000NN"}`.

```bash
curl -s http://127.0.0.1:28084/pages
```

Expected: a JSON list of pages (empty until an alert fires).

```bash
python3 -c "import json; print(json.dumps({'alert': 'A', 'severity': 'page', 'offset_seconds': 300.0}))"
```

Expected: `{"alert": "A", "severity": "page", "offset_seconds": 300.0}`.

```bash
python3 -c "import json; json.loads('{\"a\": 1,}')"
```

Expected: a `JSONDecodeError` about the trailing comma.

## Mistakes people make

- **A trailing comma** or single quotes: invalid JSON.
- **Assuming key order** matters. It does not for the data's meaning.
- **Putting JSON in a string and forgetting to escape quotes.** Inside a JSON string every `"` must be written `\"`. The dashboard's PromQL expressions show this.
- **Hand-editing large JSON.** Dashboards here are code-reviewed and linted by tests.

## Check yourself

1. Which quotes does JSON require for strings? *Double quotes.*
2. Does JSON allow comments? *No.*
3. What does `json.dumps` do? *Turns a Python structure into JSON text.*
4. How do you write a double quote inside a JSON string? *With a backslash: `\"`.*
5. Why is a trace id sent as a hex string and not a number? *It is 128 bits, which many programs cannot hold exactly as a number.*

## Further reading

- JSON specification (RFC 8259): https://www.rfc-editor.org/rfc/rfc8259
- Python `json` module: https://docs.python.org/3.12/library/json.html
