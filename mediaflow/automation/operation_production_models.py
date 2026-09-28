from __future__ import annotations

from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.production_bundle import ProductionBundle, ProductionBundleInspection


class ProductionBundleInspectArguments(DomainModel):
    bundle: ProductionBundle


ProductionBundleInspectResult = ProductionBundleInspection
