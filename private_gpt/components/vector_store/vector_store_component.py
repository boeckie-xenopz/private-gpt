import logging
import typing

from injector import inject, singleton
from llama_index.core.callbacks import CallbackManager
from llama_index.core.indices.vector_store import VectorIndexRetriever, VectorStoreIndex
from llama_index.core.vector_stores.types import (
    BasePydanticVectorStore,
    FilterCondition,
    MetadataFilter,
    MetadataFilters,
)

from private_gpt.components.embedding.embedding_component import EmbeddingComponent
from private_gpt.components.ingest.metadata_helper import MetadataKeys
from private_gpt.components.vector_store.collection_isolation import (
    AllowAllCollectionAuthorizer,
    CollectionAuthorizer,
    ensure_collection_access,
)
from private_gpt.components.vector_store.factory import (
    _PROVIDERS,
    VectorStoreFactory,
)
from private_gpt.settings.settings import Settings

logger = logging.getLogger(__name__)

DEFAULT_COLLECTION = "zgptvector"


@singleton
class VectorStoreComponent:
    _settings: Settings
    _factory: VectorStoreFactory
    _embedding_component: EmbeddingComponent

    @inject
    def __init__(
        self,
        settings: Settings,
        embedding_component: EmbeddingComponent,
    ) -> None:
        from private_gpt.components.vector_store.qdrant_factory import (
            QdrantVectorStoreFactory,
        )

        self._settings = settings
        all_providers = {"qdrant": QdrantVectorStoreFactory, **_PROVIDERS}
        provider = all_providers.get(settings.vectorstore.database)
        if provider is None:
            available = ", ".join(sorted(all_providers)) or "none"
            raise ValueError(
                f"Vector store '{settings.vectorstore.database}' is not supported. "
                f"Available: {available}"
            )

        embed_dim: int = settings.vectorstore.embed_dim
        embed_models = embedding_component.embed_models
        if len(embed_models) > 1:
            logger.warning(
                "Multiple embedding models are configured, "
                "but VectorStoreComponent only supports one. "
                "Using the first one found: %s",
                next(iter(embed_models)),
            )

        if embed_models:
            first_embed_model = next(iter(embed_models.values()))
            embed_dim = first_embed_model.embed_dim or embed_dim

        self._factory = provider(settings, embed_dim)

    @property
    def logical_multitenancy(self) -> bool:
        return self._settings.vectorstore.multitenancy == "logical"

    @property
    def default_collection(self) -> str:
        return self._settings.vectorstore.default_collection or DEFAULT_COLLECTION

    @property
    def strict_isolation(self) -> bool:
        return self._settings.vectorstore.strict_isolation

    @property
    def authorizer(self) -> CollectionAuthorizer:
        """Collection authorizer consulted before retrieval (P1.1).

        Defaults to allow-all so single-tenant deployments are unchanged. A
        multi-tenant deployment overrides this binding (or sets one via DI) with
        an implementation that maps the current Principal to entitled
        collections.
        """
        authorizer = getattr(self, "_authorizer", None)
        if authorizer is None:
            authorizer = AllowAllCollectionAuthorizer()
            self._authorizer = authorizer
        return authorizer

    def vector_store(self, collection: str) -> BasePydanticVectorStore:
        return self._factory.vector_store(collection)

    def warm_up(self) -> None:
        self._factory.warm_up()

    def get_filters(
        self,
        artifacts: list[str] | None = None,
        collection: str | None = None,
        filter_dicts: list[dict[str, typing.Any]] | None = None,
    ) -> MetadataFilters:
        filters = (
            MetadataFilters.from_dicts(filter_dicts, FilterCondition.OR)
            if filter_dicts
            else MetadataFilters(filters=[], condition=FilterCondition.OR)
        )

        if artifacts:
            for artifact in artifacts:
                filters.filters.append(
                    MetadataFilter(key=MetadataKeys.ARTIFACT_ID.value, value=artifact)
                )

        # P1.1/P1.2: fail closed before building the query. When strict isolation
        # is enabled, a missing/empty collection or an unauthorised collection is
        # refused rather than producing an unscoped (leaky) search.
        ensure_collection_access(
            self.authorizer, collection, strict=self.strict_isolation
        )

        # Apply the tenancy filter whenever a collection is given. Historically
        # this was gated on logical multitenancy only; under strict isolation it
        # is applied in every mode as defence in depth (the collection metadata
        # is present on every node regardless of physical/logical routing).
        apply_tenancy_filter = bool(collection) and (
            self.logical_multitenancy or self.strict_isolation
        )
        if apply_tenancy_filter:
            tenancy_filter = MetadataFilter(
                key=MetadataKeys.COLLECTION.value, value=collection
            )
            composite_filters: list[MetadataFilter | MetadataFilters] = (
                [tenancy_filter, filters] if filters.filters else [tenancy_filter]
            )
            filters = MetadataFilters(
                filters=composite_filters, condition=FilterCondition.AND
            )

        return filters

    def get_retriever(
        self,
        index: VectorStoreIndex,
        artifacts: list[str] | None = None,
        collection: str | None = None,
        filter_dicts: list[dict[str, typing.Any]] | None = None,
        similarity_top_k: int = 2,
        score_threshold: float | None = None,
        callback_manager: CallbackManager | None = None,
        **kwargs: typing.Any,
    ) -> VectorIndexRetriever:
        vector_store_kwargs = (
            {"score_threshold": score_threshold} if score_threshold is not None else {}
        )
        return VectorIndexRetriever(
            index=index,
            similarity_top_k=similarity_top_k,
            filters=self.get_filters(artifacts, collection, filter_dicts),
            callback_manager=callback_manager,
            vector_store_kwargs=vector_store_kwargs,
            **kwargs,
        )

    def close(self) -> None:
        factory = getattr(self, "_factory", None)
        if factory is not None and hasattr(factory, "close"):
            factory.close()

    def __del__(self) -> None:
        self.close()
