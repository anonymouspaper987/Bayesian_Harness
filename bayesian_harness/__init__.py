"""Maintenance policies for agent notes. See filters.build_filter for the registry.

The paper's method is HierarchicalHMMFilter, registered as "ours_hhmm".
"""
from .filters import (
    FilterConfig,
    # the paper's method
    HierarchicalHMMFilter,
    # baselines
    BayesianAgentFilter,
    TTLFilter,
    RandomFilter,
    OracleFilter,
    TwoCountersFilter,
    # earlier / ablation variants, kept for comparability
    CorrelatedStalenessFilter,
    FlatBetaFilter,
    DecayedBetaFilter,
    FrequencyFilter,
    FactorRuleFilter,
    ContractFilter,
    JudgeFilter,
    build_filter,
)
