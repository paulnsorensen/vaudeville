"""LLM-guided Optuna sampler with TPE fallback.

Trials 1-3 use random seed values. From trial 4 onward, an LLM
proposes example toggle configurations based on prior results.
Falls back to TPE on LLM error.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Sequence

import optuna
from optuna.distributions import BaseDistribution
from optuna.samplers import BaseSampler, RandomSampler, TPESampler
from optuna.study import Study
from optuna.trial import FrozenTrial

logger = logging.getLogger(__name__)

COLD_START_TRIALS = 3
MAX_HISTORY_TRIALS = 10


def _build_llm_prompt(
    study: Study,
    search_space: dict[str, BaseDistribution],
) -> str:
    """Build the LLM prompt from recent trial history."""
    trials = _recent_completed(study)
    history = _format_trial_history(trials)
    params = list(search_space.keys())
    return (
        "You are tuning a text classifier's prompt by toggling which "
        "examples are included.\n\n"
        f"Parameters (each is 0 or 1): {json.dumps(params)}\n\n"
        f"Recent trials (most recent last):\n{history}\n\n"
        "Propose the next configuration as a JSON object mapping each "
        "parameter name to 0 or 1. Include a one-sentence hypothesis "
        "explaining your reasoning.\n\n"
        "Respond with EXACTLY this format:\n"
        '{"config": {...}, "hypothesis": "..."}'
    )


def _recent_completed(study: Study) -> list[FrozenTrial]:
    completed = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    return completed[-MAX_HISTORY_TRIALS:]


def _format_trial_history(trials: list[FrozenTrial]) -> str:
    lines: list[str] = []
    for t in trials:
        vals = t.values or []
        val_str = ", ".join(f"{v:.4f}" for v in vals)
        src = t.user_attrs.get("proposal_source", "unknown")
        lines.append(
            f"  trial {t.number}: values=[{val_str}] "
            f"params={json.dumps(t.params)} source={src}"
        )
    return "\n".join(lines) if lines else "  (none yet)"


def _parse_llm_response(
    text: str,
    search_space: dict[str, BaseDistribution],
) -> tuple[dict[str, int], str]:
    """Parse LLM JSON response into (config, hypothesis). Raises on failure."""
    data = json.loads(text)
    config = data["config"]
    hypothesis = str(data.get("hypothesis", ""))
    result: dict[str, int] = {}
    for key in search_space:
        val = config.get(key)
        if val not in (0, 1):
            raise ValueError(f"Invalid value for {key}: {val}")
        result[key] = int(val)
    return result, hypothesis


def _call_llm(
    client: Any,
    prompt: str,
    model: str,
) -> str:
    """Call the Anthropic API and return the text response."""
    response = client.messages.create(
        model=model,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return str(response.content[0].text)


class LLMSampler(BaseSampler):
    """Optuna sampler that uses an LLM to propose example toggles.

    Cold-starts with random trials, then switches to LLM-guided
    proposals with TPE fallback on error.
    """

    def __init__(
        self,
        anthropic_client: Any = None,
        model: str = "claude-sonnet-4-20250514",
    ) -> None:
        self._client = anthropic_client
        self._model = model
        self._random = RandomSampler()
        self._tpe = TPESampler()

    def infer_relative_search_space(
        self,
        study: Study,
        trial: FrozenTrial,
    ) -> dict[str, BaseDistribution]:
        return optuna.search_space.intersection_search_space(study.trials)

    def sample_relative(
        self,
        study: Study,
        trial: FrozenTrial,
        search_space: dict[str, BaseDistribution],
    ) -> dict[str, Any]:
        if not search_space:
            return {}

        n_complete = len(_recent_completed(study))
        if n_complete < COLD_START_TRIALS:
            trial.set_user_attr("proposal_source", "random_seed")
            return self._random.sample_relative(study, trial, search_space)

        if self._client is None:
            trial.set_user_attr("proposal_source", "tpe_fallback")
            return self._tpe.sample_relative(study, trial, search_space)

        return self._sample_via_llm(study, trial, search_space)

    def _sample_via_llm(
        self,
        study: Study,
        trial: FrozenTrial,
        search_space: dict[str, BaseDistribution],
    ) -> dict[str, Any]:
        try:
            prompt = _build_llm_prompt(study, search_space)
            raw = _call_llm(self._client, prompt, self._model)
            config, hypothesis = _parse_llm_response(raw, search_space)
            trial.set_user_attr("proposal_source", "llm")
            trial.set_user_attr("hypothesis", hypothesis)
            return config
        except Exception:
            logger.warning("LLM sampling failed, falling back to TPE")
            trial.set_user_attr("proposal_source", "tpe_fallback")
            return self._tpe.sample_relative(study, trial, search_space)

    def sample_independent(
        self,
        study: Study,
        trial: FrozenTrial,
        param_name: str,
        param_distribution: BaseDistribution,
    ) -> Any:
        n_complete = len(_recent_completed(study))
        if n_complete < COLD_START_TRIALS:
            return self._random.sample_independent(
                study, trial, param_name, param_distribution
            )
        return self._tpe.sample_independent(
            study, trial, param_name, param_distribution
        )

    def after_trial(
        self,
        study: Study,
        trial: FrozenTrial,
        state: optuna.trial.TrialState,
        values: Sequence[float] | None,
    ) -> None:
        self._tpe.after_trial(study, trial, state, values)
