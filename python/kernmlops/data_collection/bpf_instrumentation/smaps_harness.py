from data_collection.bpf_instrumentation.thp_intervention_hook import (
    THPInterventionHook,
)


class SmapsHarnessHook(THPInterventionHook):
    @classmethod
    def name(cls) -> str:
        return "smaps_harness"
