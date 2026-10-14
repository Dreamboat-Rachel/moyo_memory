import argparse
import logging
import signal
import time

from moyo_memory.config import Settings
from moyo_memory.db import MemoryRepository, VectorOutboxItem, create_db_engine
from moyo_memory.semantic import (
    EmbeddingProvider,
    VectorDocument,
    VectorIndex,
    create_semantic_components,
    fact_embedding_text,
)


logger = logging.getLogger(__name__)


class VectorIndexWorker:
    def __init__(
        self,
        repository: MemoryRepository,
        embeddings: EmbeddingProvider,
        index: VectorIndex,
        *,
        batch_size: int = 64,
        max_attempts: int = 8,
    ):
        self.repository = repository
        self.embeddings = embeddings
        self.index = index
        self.batch_size = batch_size
        self.max_attempts = max_attempts

    def process_once(self) -> int:
        operations = self.repository.claim_vector_operations(
            batch_size=self.batch_size,
            max_attempts=self.max_attempts,
        )
        if not operations:
            return 0
        operation_ids = [operation.id for operation in operations]
        try:
            self._apply_operations(operations)
        except Exception as exc:
            delay = min(300, 2 ** max(operation.attempts for operation in operations))
            self.repository.fail_vector_operations(
                operation_ids,
                f"{type(exc).__name__}: {exc}",
                delay_seconds=delay,
            )
            logger.exception("Vector index batch failed; retry scheduled")
            return 0
        self.repository.complete_vector_operations(operation_ids)
        return len(operations)

    def _apply_operations(self, operations: list[VectorOutboxItem]) -> None:
        upsert_operations = [
            operation for operation in operations if operation.operation == "upsert"
        ]
        facts = self.repository.get_active_facts_by_ids(
            [operation.fact_id for operation in upsert_operations]
        )
        delete_ids = {
            operation.fact_id
            for operation in operations
            if operation.operation == "delete"
            or (
                operation.operation == "upsert"
                and operation.fact_id not in facts
            )
        }
        if delete_ids:
            self.index.delete(sorted(delete_ids))

        active_facts = [
            facts[operation.fact_id]
            for operation in upsert_operations
            if operation.fact_id in facts
        ]
        if not active_facts:
            return
        vectors = self.embeddings.embed_documents(
            [fact_embedding_text(fact) for fact in active_facts]
        )
        if len(vectors) != len(active_facts):
            raise ValueError("Embedding provider returned an unexpected vector count")
        documents = [
            VectorDocument(
                fact_id=fact.id,
                user_id=fact.user_id,
                category=fact.category.value,
                key=fact.key,
                value=fact.value,
                evidence=fact.evidence,
                confidence=fact.confidence,
                vector=vector,
            )
            for fact, vector in zip(active_facts, vectors, strict=True)
        ]
        self.index.upsert(documents)


def create_worker(settings: Settings) -> VectorIndexWorker:
    repository = MemoryRepository(
        create_db_engine(settings.database_url, echo=settings.sql_echo)
    )
    if settings.app_env == "development":
        repository.create_schema()
    repository.recover_stale_vector_operations()
    embeddings, index = create_semantic_components(settings)
    return VectorIndexWorker(
        repository,
        embeddings,
        index,
        batch_size=settings.vector_index_batch_size,
        max_attempts=settings.vector_index_max_attempts,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Process memory vector-index outbox")
    parser.add_argument("--once", action="store_true", help="Process one batch and exit")
    parser.add_argument(
        "--backfill", action="store_true", help="Enqueue all active facts before processing"
    )
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings()
    if not settings.semantic_recall_enabled:
        raise SystemExit("Set SEMANTIC_RECALL_ENABLED=true before starting the worker")
    worker = create_worker(settings)
    if args.backfill:
        count = worker.repository.enqueue_active_facts()
        logger.info("Enqueued %s active facts", count)

    if args.once:
        processed = worker.process_once()
        logger.info("Processed %s operations", processed)
        return

    running = True

    def stop(*_: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)
    logger.info("Vector index worker started")
    while running:
        processed = worker.process_once()
        if processed == 0:
            time.sleep(max(args.poll_seconds, 0.1))


if __name__ == "__main__":
    main()
