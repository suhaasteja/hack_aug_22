"""OpenTelemetry wiring shared by every module.

Traces, metrics, and logs all go to the self-hosted SigNoz collector over
OTLP. The important piece is trace propagation across the event bus: each
Event carries a `trace_context`, so a single spoken sentence can be followed
from transcript through extraction, PRD revision, research, and into an agent
being staffed in Port. That end-to-end trace is what makes the pipeline
debuggable — and it's what the alert loop reads back for context.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

log = logging.getLogger("otel")

SERVICE_NAME = "voc-factory"
_propagator = TraceContextTextMapPropagator()

_enabled = False
_tracer: trace.Tracer | None = None

# Instruments are created once at setup and reused; None until setup() runs.
segments_counter: Any = None
ideas_counter: Any = None
revisions_counter: Any = None
findings_counter: Any = None
agents_counter: Any = None
errors_counter: Any = None
llm_latency: Any = None


def setup(config: dict[str, Any]) -> None:
    """Configure OTel exporters. Safe to skip: modules degrade to no-op spans."""
    global _enabled, _tracer
    global segments_counter, ideas_counter, revisions_counter
    global findings_counter, agents_counter, errors_counter, llm_latency

    otel_cfg = config.get("observability", {})
    if not otel_cfg.get("enabled", False):
        log.info("observability disabled")
        return

    endpoint = otel_cfg.get("endpoint", "http://localhost:4317")
    resource = Resource.create(
        {
            "service.name": otel_cfg.get("service_name", SERVICE_NAME),
            "service.version": "0.1.0",
            "deployment.environment": otel_cfg.get("environment", "hackathon"),
            "voc.session_id": config.get("session", {}).get("id", "unknown"),
        }
    )

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
    )
    trace.set_tracer_provider(tracer_provider)

    metrics.set_meter_provider(
        MeterProvider(
            resource=resource,
            metric_readers=[
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=endpoint, insecure=True),
                    export_interval_millis=int(otel_cfg.get("metric_interval_ms", 10000)),
                )
            ],
        )
    )

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint, insecure=True))
    )
    logging.getLogger().addHandler(
        LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    )

    _tracer = trace.get_tracer(SERVICE_NAME)
    meter = metrics.get_meter(SERVICE_NAME)

    segments_counter = meter.create_counter(
        "voc.segments", description="Transcript segments received"
    )
    ideas_counter = meter.create_counter("voc.ideas", description="Ideas detected")
    revisions_counter = meter.create_counter("voc.prd.revisions", description="PRD revisions")
    findings_counter = meter.create_counter(
        "voc.enrichment.findings", description="Research findings accepted"
    )
    agents_counter = meter.create_counter(
        "voc.agents", description="Port agent lifecycle transitions"
    )
    # Drives the alert that closes the loop back into the factory.
    errors_counter = meter.create_counter("voc.module.errors", description="Module failures")
    llm_latency = meter.create_histogram(
        "voc.llm.duration", unit="ms", description="LLM call latency"
    )

    _enabled = True
    log.info("observability on — exporting to %s as %s", endpoint, resource.attributes["service.name"])


def inject(carrier: dict[str, str] | None = None) -> dict[str, str]:
    """Capture the current trace context for travel across the event bus."""
    carrier = carrier if carrier is not None else {}
    if _enabled:
        _propagator.inject(carrier)
    return carrier


@contextmanager
def span(
    name: str,
    *,
    parent: dict[str, str] | None = None,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """Start a span, optionally continuing a trace carried on an Event."""
    if not _enabled or _tracer is None:
        yield _NoopSpan()
        return

    ctx = _propagator.extract(parent) if parent else None
    with _tracer.start_as_current_span(name, context=ctx, attributes=attributes or {}) as s:
        try:
            yield s
        except Exception as e:
            s.record_exception(e)
            s.set_status(Status(StatusCode.ERROR, str(e)))
            raise


def record_error(module: str, operation: str, error: BaseException) -> None:
    """Count a handled failure. Handled errors never raise, so they'd otherwise be invisible."""
    if errors_counter is not None:
        errors_counter.add(
            1,
            {
                "module": module,
                "operation": operation,
                "error.type": type(error).__name__,
            },
        )
    current = trace.get_current_span()
    if current.is_recording():
        current.record_exception(error)
        current.set_status(Status(StatusCode.ERROR, f"{operation}: {error}"))


def count(instrument: Any, amount: int = 1, **attributes: Any) -> None:
    if instrument is not None:
        instrument.add(amount, attributes)


def observe(instrument: Any, value: float, **attributes: Any) -> None:
    if instrument is not None:
        instrument.record(value, attributes)


class _NoopSpan:
    def set_attribute(self, *_a: Any, **_k: Any) -> None: ...
    def set_attributes(self, *_a: Any, **_k: Any) -> None: ...
    def add_event(self, *_a: Any, **_k: Any) -> None: ...
    def record_exception(self, *_a: Any, **_k: Any) -> None: ...
    def set_status(self, *_a: Any, **_k: Any) -> None: ...
    def is_recording(self) -> bool: return False
