from __future__ import annotations

import copy


SCHEMA = "growovo.preparation-plan/1"


def _supply_keys(strategy, actions):
    keys = set()
    for wave in strategy.get("waves") or []:
        for allocation in (wave.get("allocations") or {}).values():
            for key, count in allocation.items():
                platform, action = key.split("/", 1)
                if action in actions and count:
                    keys.add(key)
    return sorted(keys)


def _task(task_id, kind, runner, depends_on, supply_keys, config=None):
    return {
        "id": task_id,
        "kind": kind,
        "runner": runner,
        "depends_on": list(depends_on),
        "supply_keys": list(supply_keys),
        "config": copy.deepcopy(config or {}),
        "status": "pending" if depends_on else "ready",
    }


def build_preparation_plan(strategy):
    preparation = strategy.get("preparation") or {}
    adapters = preparation.get("adapters") or {}
    miners = preparation.get("miners") or {}

    def generation_runner(task_id):
        adapter = adapters.get(task_id)
        if isinstance(adapter, dict):
            return str(adapter.get("runner") or "host_adapter")
        return "host_adapter" if adapter else "manual"

    def generation_config(task_id, extra=None):
        adapter = adapters.get(task_id)
        config = copy.deepcopy(adapter) if isinstance(adapter, dict) else {}
        config.pop("runner", None)
        config.update(copy.deepcopy(extra or {}))
        return config

    original_keys = _supply_keys(strategy, {"publish"})
    reply_keys = _supply_keys(strategy, {"reply"})
    dm_keys = _supply_keys(strategy, {"message"})
    tasks = [
        _task("generate-original-titles", "generate", generation_runner("generate-original-titles"),
              [], original_keys, generation_config("generate-original-titles")),
        _task("generate-original-content", "generate", generation_runner("generate-original-content"),
              ["generate-original-titles"], original_keys,
              generation_config("generate-original-content")),
        _task("review-originals-my-voice", "review", generation_runner("review-originals-my-voice"),
              ["generate-original-content"], original_keys,
              generation_config("review-originals-my-voice",
                                {"reviewer": "my-voice", "requires_source_sha256": True})),
        _task("freeze-original-supply", "freeze", "local",
              ["review-originals-my-voice"], original_keys),
        _task("mine-reply-topics", "mine", "miner", [], reply_keys,
              miners.get("reply_topics") or {}),
        _task("mine-koc", "mine", "miner", [], reply_keys,
              miners.get("koc") or {}),
        _task("qualify-reply-targets", "qualify", "local",
              ["mine-reply-topics", "mine-koc"], reply_keys),
        _task("draft-replies", "generate", generation_runner("draft-replies"),
              ["qualify-reply-targets"], reply_keys, generation_config("draft-replies")),
        _task("review-reply-strategy", "review", generation_runner("review-reply-strategy"),
              ["draft-replies"], reply_keys,
              generation_config("review-reply-strategy", {"requires_source_sha256": True})),
        _task("freeze-reply-supply", "freeze", "local",
              ["review-reply-strategy"], reply_keys),
        _task("mine-dm-prospects", "mine", "miner", [], dm_keys,
              miners.get("dm_prospects") or {}),
        _task("qualify-dm-prospects", "qualify", "local",
              ["mine-dm-prospects"], dm_keys),
        _task("draft-dms", "generate", generation_runner("draft-dms"),
              ["qualify-dm-prospects"], dm_keys, generation_config("draft-dms")),
        _task("review-dm-content", "review", generation_runner("review-dm-content"),
              ["draft-dms"], dm_keys,
              generation_config("review-dm-content", {"requires_source_sha256": True})),
        _task("freeze-dm-supply", "freeze", "local",
              ["review-dm-content"], dm_keys),
    ]
    return {
        "schema": SCHEMA,
        "tasks": tasks,
        "adapters": copy.deepcopy(adapters),
        "policy": {
            "candidate_count_is_not_ready_supply": True,
            "freeze_before_wave_approval": True,
            "resume_after_reconciliation": True,
        },
    }
